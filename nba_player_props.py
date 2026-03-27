import os
import json
import time
import logging
import asyncio
import httpx
from dotenv import load_dotenv
from nba_api.stats.static import players as nba_players
from nba_api.stats.endpoints import playergamelog

load_dotenv()
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4"

# Cache: player game logs
_gamelog_cache = {}  # key -> {data, timestamp}
GAMELOG_TTL = 3600   # 1 hour

# Cache: event props
_props_cache = {}    # event_id -> {data, timestamp}
PROPS_TTL = 1800     # 30 min

# Strict sequential pacing to avoid Cloudflare WAF blackhole
NBA_SEMAPHORE = asyncio.Semaphore(1)

async def get_nba_event_props(event_id: str) -> list:
    now = time.time()
    if event_id in _props_cache and now - _props_cache[event_id]["ts"] < PROPS_TTL:
        return _props_cache[event_id]["data"]

    markets = "player_points,player_rebounds,player_assists"
    url = f"{BASE_URL}/sports/basketball_nba/events/{event_id}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": markets,
        "oddsFormat": "decimal"
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logging.warning(f"Player props API returned {resp.status_code} for event {event_id}")
                return []
            data = resp.json()
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

def _fetch_player_gamelog_sync(player_id: int, stat: str, games: int) -> dict:
    """Senkron olarak nba_api kutuphanesini kullanip veri ceker."""
    try:
        # NBA API'ye ard arda istek atmamak icin siki bekleme (WAF atlatma)
        time.sleep(1.2)
        
        gl = playergamelog.PlayerGameLog(
            player_id=player_id,
            season="2025-26",
            timeout=15
        ).get_data_frames()[0]

        if gl.empty:
            return {"avg": None, "last_games": [], "player_id": player_id}

        recent_df = gl.head(games)
        recent = recent_df[stat].tolist()
        avg = round(sum(recent) / len(recent), 1) if recent else None
        
        last_games_detail = []
        for _, row in recent_df.iterrows():
            last_games_detail.append({
                "date": row["GAME_DATE"],
                "matchup": row["MATCHUP"],
                "value": row[stat],
                "pts": int(row.get("PTS", 0)),
                "reb": int(row.get("REB", 0)),
                "ast": int(row.get("AST", 0))
            })

        return {"avg": avg, "last_games": recent, "last_games_detail": last_games_detail, "player_id": player_id}

    except Exception as e:
        logging.warning(f"Game log fetch exception for player_id={player_id}: {type(e).__name__} - {str(e)[:50]}")
        return {"avg": None, "last_games": [], "last_games_detail": [], "player_id": player_id}

async def _fetch_player_gamelog_async(player_id: int, stat: str, games: int) -> dict:
    cache_key = f"{player_id}_{stat}_{games}"
    now = time.time()
    if cache_key in _gamelog_cache and now - _gamelog_cache[cache_key]["ts"] < GAMELOG_TTL:
        return _gamelog_cache[cache_key]["data"]

    # Strict semaphore blocks concurrent stats.nba.com entry entirely
    async with NBA_SEMAPHORE:
        result = await asyncio.to_thread(_fetch_player_gamelog_sync, player_id, stat, games)
        _gamelog_cache[cache_key] = {"data": result, "ts": now}
        return result

async def get_player_recent_avg(player_name: str, stat: str, games: int = 5) -> dict:
    matched = nba_players.find_players_by_full_name(player_name)
    if not matched:
        last_name = player_name.split()[-1]
        matched = [p for p in nba_players.get_active_players()
                   if last_name.lower() in p['full_name'].lower()]

    if not matched:
        return {"avg": None, "last_games": [], "player_id": None}

    player_id = matched[0]["id"]
    return await _fetch_player_gamelog_async(player_id, stat, games)

async def evaluate_prop(player_name: str, stat: str, line: float,
                        over_odds: float, recent_games: int = 3) -> dict:
    # Kullanıcı 5 maçlık detay istediği için her zaman 5 çekiyoruz
    form = await get_player_recent_avg(player_name, stat, games=5)
    all_last_games = form.get("last_games", [])

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

        return {
            "recommendation": "OVER",
            "line": line,
            "over_odds": over_odds,
            "avg_last_n": avg_3,
            "deficit": round(deficit, 1),
            "games_below": games_below,
            "confidence": confidence,
            "last_games_detail": form.get("last_games_detail", []), # 5 maçın detayı buraya düşer
            "reason": f"Son {recent_games} macta ort. {avg_3:.1f} (Hat: {line}) — {deficit:.1f} eksik. Patlama bekleniyor! 🔥"
        }

    return {"recommendation": None}

async def analyze_nba_player_props(event_id: str, home_team: str, away_team: str) -> list:
    props = await get_nba_event_props(event_id)
    if not props:
        return []

    logging.info(f"Analyzing {len(props)} props for {home_team} vs {away_team} (strictly sequential)")
    recommendations = []

    async def analyze_one(prop):
        player = prop["player"]
        stat = prop["stat"]
        line = prop["line"]
        over_odds = prop["over_odds"]

        try:
            result = await evaluate_prop(player, stat, line, over_odds, 3)
            
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
                    "bet_target": f"{player} | {stat} OVER {line}",
                    "bet_amount": bet_amount
                })
        except Exception as e:
            logging.warning(f"Error evaluating prop for {player}: {e}")

    # Gather parallel but strictly gated by Semaphore(1)
    await asyncio.gather(*(analyze_one(p) for p in props))

    logging.info(f"Props result: {len(recommendations)} recs from {len(props)} ({home_team} vs {away_team})")
    return sorted(recommendations, key=lambda x: -x["confidence"])
