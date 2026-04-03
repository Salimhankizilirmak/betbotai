import os
import json
import time
import logging
import asyncio
import httpx
from dotenv import load_dotenv
from nba_api.stats.static import players as nba_players
from nba_api.stats.endpoints import playergamelog
from api_key_manager import odds_api_manager
import nba_data
from bet_manager import get_db_connection


load_dotenv()
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
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            max_retries = odds_api_manager.get_max_retries()
            for attempt in range(max_retries):
                api_key = odds_api_manager.get_current_key()
                params = {
                    "apiKey": api_key,
                    "regions": "us",
                    "markets": markets,
                    "oddsFormat": "decimal"
                }
                resp = await client.get(url, params=params)
                
                if resp.status_code in [401, 403, 429]:
                    logging.warning(f"get_nba_event_props: API Key {api_key[:6]}... failed ({resp.status_code}). Rotating...")
                    odds_api_manager.rotate_key()
                    await asyncio.sleep(1)
                    continue
                    
                if resp.status_code != 200:
                    logging.warning(f"Player props API returned {resp.status_code} for event {event_id}")
                    return []
                
                data = resp.json()
                props = _parse_props(data)
                _props_cache[event_id] = {"data": props, "ts": now}
                return props
            return []
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
                        over_odds: float, opponent: str = "", recent_games: int = 3) -> dict:
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

    logging.info(f"Analyzing {len(props)} props for {home_team} vs {away_team} (strictly sequential)")
    recommendations = []

    async def analyze_one(prop):
        player = prop["player"]
        stat = prop["stat"]
        line = prop["line"]
        over_odds = prop["over_odds"]
        
        # Find opponent
        opponent = away_team if player in home_team else home_team # Basit bir tahmin, ideal senaryoda api'den tam takım ismi de gelebilir

        try:
            result = await evaluate_prop(player, stat, line, over_odds, opponent, 3)
            
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

    # Gather parallel but strictly gated by Semaphore(1)
    await asyncio.gather(*(analyze_one(p) for p in props))

    logging.info(f"Props result: {len(recommendations)} recs from {len(props)} ({home_team} vs {away_team})")
    return sorted(recommendations, key=lambda x: -x["confidence"])

