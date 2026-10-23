import os
import json
import time
import logging
import asyncio
from dotenv import load_dotenv
from nba_api.stats.static import teams as nba_teams  # statik takım listesi (ağ isteği yok)
import espn_nba
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

async def evaluate_prop(player_name: str, stat: str, line: float,
                        over_odds: float, home_team: str = "", away_team: str = "",
                        recent_games: int = 3) -> dict:
    # Kullanıcı 5 maçlık detay istediği için her zaman 5 çekiyoruz
    form = await get_player_recent_avg(player_name, stat, games=5)
    all_last_games = form.get("last_games", [])
    opponent = _find_opponent(form.get("last_games_detail", []), home_team, away_team)

    if len(all_last_games) < recent_games:
        return {"recommendation": None, "reason": "Veri yok", "confidence": 0}

    # Çekirdek algoritma hala son 'recent_games' (3) maçlık dar veriye bakar
    last_games_3 = all_last_games[:recent_games]
    avg_3 = round(sum(last_games_3) / len(last_games_3), 1)

    deficit = line - avg_3
    games_below = sum(1 for g in last_games_3 if g < line)

    if games_below >= 2 and deficit > 0:
        pct_below = (deficit / line) * 100
        confidence = min(90, int(50 + pct_below))

        # 1. VERİTABANI ÖĞRENME (Geçmiş Hatalardan Ders Çıkarma)
        past_losses = 0
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM bets WHERE match_id LIKE %s AND status = 'LOST' ORDER BY created_at DESC LIMIT 3", (f"PROP_%_{player_name}_{stat}",))
                past_losses = len(cursor.fetchall())
        except Exception as e:
            logging.error(f"Prop memory fetch error: {e}")

        penalty_reason = ""
        if past_losses > 0:
            penalty = past_losses * 15 # Her kayıp için 15 puan sil
            confidence -= penalty
            penalty_reason = f" [! {past_losses} eski kayıptan {-penalty} ceza !]"
            if past_losses >= 2:
                # 2 kere kaybettiyse tamamen pas geç
                return {"recommendation": None, "reason": "Kara liste (Üst üste kayıp)", "confidence": 0}

        # 2. RAKİP EŞLEŞMESİ (Zorluk Analizi)
        opp_modifier = ""
        if opponent:
            df = nba_data.fetch_nba_standings()
            if df is not None:
                try:
                    opp_row = df[df['TeamName'].str.contains(opponent.split()[-1], case=False, na=False)]
                    if not opp_row.empty:
                        win_pct = float(opp_row.iloc[0].get('WinPCT', '0.50'))
                        # Defansif zoru win_pct basitleştirilmiş proxy olarak al: win_pct > 0.6 zorlu, < 0.4 kolay
                        if win_pct > 0.60:
                            confidence -= 8
                            opp_modifier = f" (Rakip '{opponent}' formda/zorlu -8 Güven)"
                        elif win_pct < 0.40:
                            confidence += 5
                            opp_modifier = f" (Rakip '{opponent}' zayıf, +5 Güven)"
                except:
                    pass

        return {
            "recommendation": "OVER",
            "line": line,
            "over_odds": over_odds,
            "avg_last_n": avg_3,
            "deficit": round(deficit, 1),
            "games_below": games_below,
            "confidence": confidence,
            "last_games_detail": form.get("last_games_detail", []), # 5 maçın detayı buraya düşer
            "reason": f"Son {recent_games} macta ort. {avg_3:.1f} (Hat: {line}) — {deficit:.1f} eksik.{penalty_reason}{opp_modifier} Patlama bekleniyor! 🔥"
        }

    return {"recommendation": None}

async def analyze_nba_player_props(event: dict) -> list:
    event_id = event["id"]
    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")

    props = await get_nba_event_props(event_id)
    if not props:
        return []
    await asyncio.to_thread(preload_player_logs, list({p["player"] for p in props}))

    logging.info(f"Analyzing {len(props)} props for {home_team} vs {away_team} (strictly sequential)")
    recommendations = []

    async def analyze_one(prop):
        player = prop["player"]
        stat = prop["stat"]
        line = prop["line"]
        over_odds = prop["over_odds"]

        try:
            result = await evaluate_prop(player, stat, line, over_odds, home_team, away_team, 3)

            if result.get("recommendation") == "OVER" and result.get("confidence", 0) >= 65:
                # Dynamic bet sizing for player props (max 500 BB)
                conf = result["confidence"]
                if conf >= 85:
                    bet_amount = 500  # Tam gaz!
                elif conf >= 75:
                    bet_amount = 300
                elif conf >= 68:
                    bet_amount = 150
                else:
                    bet_amount = 50

                recommendations.append({
                    "player": player,
                    "stat": stat,
                    "market": prop["market"],
                    "line": line,
                    "over_odds": over_odds,
                    "confidence": conf,
                    "avg_last_n": result.get("avg_last_n"),
                    "deficit": result.get("deficit"),
                    "last_games_detail": result.get("last_games_detail", []),
                    "reason": result["reason"],
                    "event_id": event_id,
                    "home_team": home_team,
                    "away_team": away_team,
                    "commence_time": event.get("commence_time"),
                    "bet_target": f"{player} | {stat} OVER {line}",
                    "bet_amount": bet_amount
                })
        except Exception as e:
            logging.warning(f"Error evaluating prop for {player}: {e}")

    await asyncio.gather(*(analyze_one(p) for p in props))

    # 3. FILTRELEME MANTIGI (Baron Kuralları)
    # - Güvene göre sırala
    recommendations.sort(key=lambda x: -x["confidence"])

    final_selections = []
    used_players = set()
    used_stats = set()

    for rec in recommendations:
        # En fazla 3 oyuncu
        if len(final_selections) >= 3:
            break

        player = rec["player"]
        stat = rec["stat"]

        # Kural 1: Aynı oyuncuya birden fazla bahis alınmaz
        if player in used_players:
            continue

        # Kural 2: Aynı maçta farklı 2 oyuncuya aynı kategori (PTS/REB/AST) oynanmaz
        if stat in used_stats:
            continue

        # Seçime ekle ve işaretle
        final_selections.append(rec)
        used_players.add(player)
        used_stats.add(stat)

    logging.info(f"Props result: {len(final_selections)} filtered recs from {len(recommendations)} candidates ({home_team} vs {away_team})")
    return final_selections

