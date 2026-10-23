import os
import json
import time
import logging
import asyncio
from dotenv import load_dotenv
from nba_api.stats.static import teams as nba_teams  # statik takım listesi (ağ isteği yok)
import espn_nba
import numpy as np
import pandas as pd
import strategy
from market.devig import devig
from market.edge import expected_value
from models.nba_props_model import PropModel
from oddsapi_client import get_event_odds
from strategy import NBA_PROP_MARKETS, PROPS_REGION
import nba_data
from seasons import nba_season
from bet_manager import get_db_connection


load_dotenv()

# Cache: player game logs
GAMELOG_TTL = 3600   # 1 hour

# Cache: event props
_props_cache = {}    # event_id -> {data, timestamp}
PROPS_TTL = 1800     # 30 min

async def get_nba_event_props(event_id: str) -> list:
    now = time.time()
    if event_id in _props_cache and now - _props_cache[event_id]["ts"] < PROPS_TTL:
        return _props_cache[event_id]["data"]

    try:
        # get_event_odds yanıtı odds_snapshots tablosuna da kaydeder (prop çizgisi geçmişi)
        data = await get_event_odds("basketball_nba", event_id, ",".join(NBA_PROP_MARKETS), region=PROPS_REGION)
        if not data:
            logging.warning(f"Player props API returned no data for event {event_id}")
            return []
        props = _parse_props(data)
        _props_cache[event_id] = {"data": props, "ts": now}
        return props
    except Exception as e:
        logging.error(f"Player props fetch error: {e}")
        return []

def _parse_props(event_data: dict) -> list:
    props = []
    for bookmaker in event_data.get("bookmakers", [])[:2]:  # top 2 bookmakers
        for market in bookmaker.get("markets", []):
            mkey = market.get("key", "")
            if mkey not in ("player_points", "player_rebounds", "player_assists"):
                continue

            category_map = {
                "player_points": "PTS",
                "player_rebounds": "REB",
                "player_assists": "AST"
            }
            stat = category_map[mkey]

            player_data = {}
            for outcome in market.get("outcomes", []):
                name = outcome.get("description") or outcome.get("name", "")
                side = outcome.get("name", "")
                point = outcome.get("point", 0)
                price = outcome.get("price", 0)

                if name not in player_data:
                    player_data[name] = {"line": point, "over_odds": None, "under_odds": None}
                if "over" in side.lower():
                    player_data[name]["over_odds"] = price
                    player_data[name]["line"] = point
                elif "under" in side.lower():
                    player_data[name]["under_odds"] = price

            for player_name, pdata in player_data.items():
                already = any(p["player"] == player_name and p["market"] == mkey for p in props)
                if not already and pdata["over_odds"] and pdata["line"]:
                    props.append({
                        "player": player_name,
                        "book": bookmaker.get("key"),
                        "market": mkey,
                        "stat": stat,
                        "line": pdata["line"],
                        "over_odds": pdata["over_odds"],
                        "under_odds": pdata["under_odds"]
                    })
    return props

# Oyuncu maç kayıtları ESPN box score'larından Supabase'e biriktirilir (espn_nba.py);
# stats.nba.com bulut IP'lerini engellediği için nba_api game log kullanılmaz.
_logs_cache = {}     # player_key -> {"rows": [...], "ts": float}

def preload_player_logs(player_names, games=5):
    """Bir maçtaki tüm prop oyuncularının son maçlarını tek sorguda önbelleğe alır."""
    rows_by_key = espn_nba.player_logs(player_names, limit=games, fill_previous_season=True)
    now = time.time()
    for name in player_names:
        k = espn_nba.player_key(name)
        _logs_cache[k] = {"rows": rows_by_key.get(k, []), "ts": now}

def _logs_to_form(rows, stat):
    col = stat.lower()  # PTS -> pts
    recent = [r[col] or 0 for r in rows]
    detail = [{
        "date": str(r["game_date"]),
        "matchup": f"{r['team_abbr']} {'vs.' if r['is_home'] else '@'} {r['opponent_abbr']}",
        "team_name": r["team_name"],
        "opponent_name": r["opponent_name"],
        "season": r["season"],
        "value": r[col] or 0,
        "pts": r["pts"] or 0,
        "reb": r["reb"] or 0,
        "ast": r["ast"] or 0,
    } for r in rows]
    avg = round(sum(recent) / len(recent), 1) if recent else None
    return {"avg": avg, "last_games": recent, "last_games_detail": detail, "player_id": None}

async def get_player_recent_avg(player_name: str, stat: str, games: int = 5) -> dict:
    k = espn_nba.player_key(player_name)
    cached = _logs_cache.get(k)
    if not cached or time.time() - cached["ts"] > GAMELOG_TTL:
        await asyncio.to_thread(preload_player_logs, [player_name], games)
        cached = _logs_cache.get(k, {"rows": []})
    return _logs_to_form(cached["rows"][:games], stat)

def _team_abbreviation(team_name: str):
    found = nba_teams.find_teams_by_full_name(team_name) if team_name else []
    return found[0]["abbreviation"] if found else None

def _find_opponent(last_games_detail: list, home_team: str, away_team: str) -> str:
    """Oyuncunun takımını son maçından bulur (takım adı, yoksa 'LAL vs. BOS' MATCHUP kısaltması), rakibi döner."""
    if not last_games_detail:
        return ""
    team_name = last_games_detail[0].get("team_name")
    if team_name:
        if nba_data.team_match(team_name, home_team):
            return away_team
        if nba_data.team_match(team_name, away_team):
            return home_team
    player_abbr = str(last_games_detail[0].get("matchup", "")).split(" ")[0]
    if player_abbr and player_abbr == _team_abbreviation(home_team):
        return away_team
    if player_abbr and player_abbr == _team_abbreviation(away_team):
        return home_team
    return ""  # Takas vb. nedeniyle bilinemiyorsa rakip düzeltmesi uygulanmaz

# ---------------------------------------------------------------- projeksiyon modeli (models/nba_props_model.py)
MODEL_TTL = 3600
_model_cache = {"model": None, "ts": 0.0}
STAT_COL = {"PTS": "pts", "REB": "reb", "AST": "ast"}

def get_model():
    """Bu ve geçen sezonun box score'larıyla modeli bir kez yükler (props turu boyunca tekrar kullanılır)."""
    if _model_cache["model"] is None or time.time() - _model_cache["ts"] > MODEL_TTL:
        season = nba_season()
        rows = espn_nba.season_rows([season, espn_nba.previous_season(season)])
        _model_cache.update(model=PropModel(rows), ts=time.time())
    return _model_cache["model"]

def _team_abbr_lookup(model, team_name):
    hist = model.history
    for name, abbr in hist[["team_name", "team_abbr"]].drop_duplicates("team_name").itertuples(index=False):
        if nba_data.team_match(name, team_name):
            return abbr
    return None

def evaluate_prop_model(model, prop, home_team, away_team, commence_time):
    """
    Bir prop için model olasılığı vs marjsız piyasa olasılığı. İki taraf da değerlendirilir; beklenen değeri
    yüksek olan taraf döner (eşikleri geçmese bile — kayıt için). Model yoksa / veri yetersizse None.
    """
    key = espn_nba.player_key(prop["player"])
    hist = model.history[model.history["player_key"] == key]
    if hist.empty:
        return None
    team_name = hist["team_name"].iloc[-1]
    if nba_data.team_match(team_name, home_team):
        opponent, is_home = away_team, True
    elif nba_data.team_match(team_name, away_team):
        opponent, is_home = home_team, False
    else:
        return None  # takas/kadro değişikliği: oyuncunun takımı bu maçta değil
    opp_abbr = _team_abbr_lookup(model, opponent)
    game_date = espn_nba.us_game_date(commence_time) if commence_time else espn_nba.today_et()
    proj = model.project(key, game_date, opp_abbr, is_home=is_home, season=nba_season(game_date))
    col = STAT_COL[prop["stat"]]
    if not proj or proj.get(f"mu_{col}") is None:
        return None
    mu, line = proj[f"mu_{col}"], float(prop["line"])
    p_over, p_under = model.probabilities(col, mu, line)
    over_odds, under_odds = prop.get("over_odds"), prop.get("under_odds")
    if over_odds and under_odds and over_odds > 1 and under_odds > 1:
        fair_over, fair_under = devig([over_odds, under_odds], "power")
    else:
        fair_over = fair_under = None
    sides = []
    lam = strategy.PROP_MARKET_SHRINK
    for side, p, fair, odds in (("OVER", p_over, fair_over, over_odds), ("UNDER", p_under, fair_under, under_odds)):
        if odds and odds > 1 and fair is not None:
            # Bahis kararı, piyasaya doğru çekilmiş olasılıkla verilir (ham model sadece kayıt/ölçüm için)
            p_adj = fair + lam * (p - fair)
            sides.append({"side": side, "p_raw": p, "p_model": p_adj, "p_fair": fair, "odds": float(odds),
                          "raw_edge": p - fair, "edge": p_adj - fair, "ev": expected_value(p_adj, odds)})
    if not sides:
        return None
    best = max(sides, key=lambda s: s["ev"])
    last5 = hist.tail(5).iloc[::-1]
    season_games = int((hist["season"] == nba_season(game_date)).sum())
    return {**best, "mu": mu, "line": line, "min_proj": proj["min_proj"], "n_prev": proj["n_prev"],
            "season_games": season_games,
            "opp_factor": proj[f"opp_factor_{col}"], "opponent": opponent, "opp_abbr": opp_abbr,
            "last5": [_row_to_detail(r, col) for r in last5.to_dict("records")]}

def _row_to_detail(r, col):
    return {"date": str(pd.Timestamp(r["game_date"]).date()),
            "matchup": f"{r['team_abbr']} {'vs.' if r['is_home'] else '@'} {r['opponent_abbr']}",
            "team_name": r["team_name"], "opponent_name": r["opponent_name"], "season": r["season"],
            "minutes": r["minutes"], "value": r[col], "pts": r["pts"], "reb": r["reb"], "ast": r["ast"]}

def passes_thresholds(ev_):
    """Bahis eşiği: model edge'i, pozitif beklenti, yeterli geçmiş ve dakika, makul oran."""
    return (ev_["edge"] >= strategy.PROP_MIN_EDGE and ev_["ev"] >= strategy.PROP_MIN_EV
            and ev_["raw_edge"] <= strategy.PROP_MAX_RAW_EDGE
            and ev_["n_prev"] >= strategy.PROP_MIN_GAMES and ev_["season_games"] >= strategy.PROP_MIN_SEASON_GAMES
            and ev_["min_proj"] >= strategy.PROP_MIN_MINUTES
            and strategy.PROP_ODDS_MIN <= ev_["odds"] <= strategy.PROP_ODDS_MAX)

def _record_predictions(model, rows):
    """Değerlendirilen tüm prop'ları predictions tablosuna yazar (sonradan isabet/CLV ölçümü için)."""
    if not rows:
        return
    try:
        from psycopg2.extras import execute_values, Json
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO predictions (model_name, model_version, sport_key, event_id, commence_time, market,
                        selection, player, point, p_model, p_market_fair, price, edge, meta) VALUES %s
                """, [("nba_props", model.params.get("version", "?"), "basketball_nba", r["event_id"], r["commence_time"],
                       r["market"], r["side"].title(), r["player"], r["line"], r["p_raw"], r["p_fair"], r["odds"],
                       r["raw_edge"], Json({"mu": r["mu"], "min_proj": r["min_proj"], "n_prev": r["n_prev"],
                                            "season_games": r["season_games"], "opp_factor": r["opp_factor"],
                                            "p_adj": r["p_model"], "edge_adj": r["edge"], "ev_adj": r["ev"],
                                            "shrink": strategy.PROP_MARKET_SHRINK})) for r in rows])
            conn.commit()
    except Exception as e:
        logging.error(f"predictions yazma hatası: {e}")

async def analyze_nba_player_props(event: dict) -> list:
    event_id = event["id"]
    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")
    commence_time = event.get("commence_time")

    props = await get_nba_event_props(event_id)
    if not props:
        return []
    model = await asyncio.to_thread(get_model)

    logging.info(f"Analyzing {len(props)} props for {home_team} vs {away_team} (model {model.params.get('version')})")
    evaluated, recommendations = [], []
    for prop in props:
        try:
            r = evaluate_prop_model(model, prop, home_team, away_team, commence_time)
        except Exception as e:
            logging.warning(f"Error evaluating prop for {prop['player']}: {e}")
            continue
        if not r:
            continue
        row = {**r, "player": prop["player"], "stat": prop["stat"], "market": prop["market"],
               "event_id": event_id, "commence_time": commence_time}
        evaluated.append(row)
        if not passes_thresholds(r):
            continue
        stat_tr = {"PTS": "sayı", "REB": "ribaund", "AST": "asist"}[prop["stat"]]
        side_tr = "ÜST" if r["side"] == "OVER" else "ALT"
        reason = (f"Projeksiyon: {r['min_proj']:.0f} dk, {r['mu']:.1f} {stat_tr} (çizgi {r['line']}). "
                  f"Model P({side_tr}) %{r['p_raw'] * 100:.0f}, piyasa %{r['p_fair'] * 100:.0f}, "
                  f"piyasaya çekilmiş %{r['p_model'] * 100:.0f} → edge +{r['edge'] * 100:.1f} puan, "
                  f"beklenti +%{r['ev'] * 100:.1f}. Rakip ({r['opp_abbr']}) faktörü {r['opp_factor']:.2f}, "
                  f"bu sezon {r['season_games']} maç ({r['n_prev']} toplam).")
        recommendations.append({
            "player": prop["player"],
            "book": prop.get("book"),
            "stat": prop["stat"],
            "market": prop["market"],
            "line": r["line"],
            "side": r["side"],
            "odds": r["odds"],
            "over_odds": prop.get("over_odds"),
            "under_odds": prop.get("under_odds"),
            "p_model": round(r["p_model"], 4),
            "p_raw": round(r["p_raw"], 4),
            "raw_edge": round(r["raw_edge"], 4),
            "season_games": r["season_games"],
            "p_fair": round(r["p_fair"], 4),
            "edge": round(r["edge"], 4),
            "ev": round(r["ev"], 4),
            "mu": round(r["mu"], 2),
            "min_proj": round(r["min_proj"], 1),
            "confidence": int(round(r["p_model"] * 100)),
            "avg_last_n": round(float(np.mean([g["value"] for g in r["last5"]])), 1) if r["last5"] else None,
            "deficit": round(r["line"] - r["mu"], 1),
            "last_games_detail": r["last5"],
            "reason": reason,
            "event_id": event_id,
            "home_team": home_team,
            "away_team": away_team,
            "commence_time": commence_time,
            "bet_target": f"{prop['player']} | {prop['stat']} {r['side']} {r['line']}",
        })
    await asyncio.to_thread(_record_predictions, model, evaluated)

    # Seçim kuralları: en yüksek edge'den başla; maç başına en fazla 3, aynı oyuncuya ve aynı kategoriye bir kez
    recommendations.sort(key=lambda x: -x["edge"])
    final_selections, used_players, used_stats = [], set(), set()
    for rec in recommendations:
        if len(final_selections) >= 3:
            break
        if rec["player"] in used_players or rec["stat"] in used_stats:
            continue
        final_selections.append(rec)
        used_players.add(rec["player"])
        used_stats.add(rec["stat"])

    logging.info(f"Props result: {len(final_selections)} selected / {len(recommendations)} with edge / "
                 f"{len(evaluated)} evaluated ({home_team} vs {away_team})")
    return final_selections
