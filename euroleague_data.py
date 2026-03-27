import httpx
import logging
import asyncio
from difflib import SequenceMatcher

# Euroleague API Feeds (Internal Incrowd Feeds)
URL_PLAYER_STATS = "https://feeds.incrowdsports.com/provider/euroleague-feeds/v3/competitions/E/statistics/players/traditional?seasonMode=All&limit=400&sortDirection=descending&statisticMode=perGame&statisticSortMode=perGame"
URL_TEAM_SCORE = "https://feeds.incrowdsports.com/provider/euroleague-feeds/v2/competitions/E/stats/clubs/leaders?category=Score&seasonMode=All&limit=200&aggregate=perGame"
URL_TEAM_REBOUNDS = "https://feeds.incrowdsports.com/provider/euroleague-feeds/v2/competitions/E/stats/clubs/leaders?category=TotalRebounds&seasonMode=All&limit=200&aggregate=perGame"
URL_TEAM_ASSISTS = "https://feeds.incrowdsports.com/provider/euroleague-feeds/v2/competitions/E/stats/clubs/leaders?category=Assists&seasonMode=All&limit=200&aggregate=perGame"

# Caches
_euro_cache = {
    "players": None,
    "teams_pts": None,
    "teams_reb": None,
    "teams_ast": None,
    "last_updated": 0
}

async def fetch_euroleague_json(url: str):
    """Generic async fetcher for Euroleague feeds."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"Euroleague API error ({response.status_code}) for {url}")
                return None
    except Exception as e:
        logging.error(f"Error fetching Euroleague data: {e}")
        return None

async def refresh_euroleague_cache():
    """Refreshes all Euroleague stats from feeds."""
    import time
    if time.time() - _euro_cache["last_updated"] < 14400: # 4 saatlik cache
        return

    logging.info("Refreshing Euroleague Statistics Cache...")
    
    # Parallel fetching
    tasks = [
        fetch_euroleague_json(URL_PLAYER_STATS),
        fetch_euroleague_json(URL_TEAM_SCORE),
        fetch_euroleague_json(URL_TEAM_REBOUNDS),
        fetch_euroleague_json(URL_TEAM_ASSISTS)
    ]
    
    results = await asyncio.gather(*tasks)
    
    if results[0]: _euro_cache["players"] = results[0]
    if results[1]: _euro_cache["teams_pts"] = results[1]
    if results[2]: _euro_cache["teams_reb"] = results[2]
    if results[3]: _euro_cache["teams_ast"] = results[3]
    _euro_cache["last_updated"] = time.time()
    
    logging.info(f"Euroleague Cache Refreshed. Players: {len(_euro_cache['players'].get('players', [])) if _euro_cache['players'] else 0}")

def fuzzy_match(name1, name2, threshold=0.7):
    """Basic fuzzy matching for team names."""
    if not name1 or not name2: return False
    n1, n2 = name1.lower(), name2.lower()
    if n1 in n2 or n2 in n1: return True
    return SequenceMatcher(None, n1, n2).ratio() >= threshold

async def get_euroleague_team_stats(team_name: str):
    """Returns a summary of team stats (PPG, RPG, APG)."""
    await refresh_euroleague_cache()
    
    ppg, rpg, apg = 0, 0, 0
    found_any = False

    try:
        # 1. Try Team Feeds first
        if _euro_cache["teams_pts"] and "data" in _euro_cache["teams_pts"]:
            for item in _euro_cache["teams_pts"].get("data", []):
                name_in_api = item.get("clubName") or item.get("club", {}).get("name", "")
                if fuzzy_match(team_name, name_in_api):
                    ppg = item.get("averagePerGame", 0)
                    found_any = True
                    break
        
        if _euro_cache["teams_reb"] and "data" in _euro_cache["teams_reb"]:
            for item in _euro_cache["teams_reb"].get("data", []):
                name_in_api = item.get("clubName") or item.get("club", {}).get("name", "")
                if fuzzy_match(team_name, name_in_api):
                    rpg = item.get("averagePerGame", 0)
                    break

        if _euro_cache["teams_ast"] and "data" in _euro_cache["teams_ast"]:
            for item in _euro_cache["teams_ast"].get("data", []):
                name_in_api = item.get("clubName") or item.get("club", {}).get("name", "")
                if fuzzy_match(team_name, name_in_api):
                    apg = item.get("averagePerGame", 0)
                    break

        # 2. Fallback to Player Aggregation if stats are missing
        if not found_any or apg == 0:
            if _euro_cache["players"] and "players" in _euro_cache["players"]:
                team_players = [p for p in _euro_cache["players"].get("players", []) 
                               if fuzzy_match(team_name, p.get("player", {}).get("team", {}).get("name", ""))]
                if team_players:
                    # Sort by minutes to get active roster (top 12)
                    team_players.sort(key=lambda x: -x.get("minutesPlayed", 0))
                    active_roster = team_players[:12]
                    
                    calc_ppg = sum(p.get("pointsScored", 0) for p in active_roster)
                    calc_rpg = sum(p.get("totalRebounds", 0) for p in active_roster)
                    calc_apg = sum(p.get("assists", 0) for p in active_roster)
                    
                    # If team feed failed, use these
                    if ppg == 0: ppg = calc_ppg
                    if rpg == 0: rpg = calc_rpg
                    if apg == 0: apg = calc_apg
                    found_any = True

        if not found_any:
            return f"No Euroleague stats found for {team_name}."
            
        return f"Euroleague Stats - PPG: {ppg:.1f}, RPG: {rpg:.1f}, APG: {apg:.1f}."
    except Exception as e:
        return f"Error parsing Euroleague stats: {e}"
    except Exception as e:
        return f"Error parsing Euroleague team stats: {e}"

async def get_euroleague_player_trends(team_name: str):
    """Returns top player trends for a team."""
    await refresh_euroleague_cache()
    
    if not _euro_cache["players"] or "players" not in _euro_cache["players"]:
        return "Euroleague player trends currently unavailable."

    try:
        # Filter players by team
        team_players = []
        for p_item in _euro_cache["players"].get("players", []):
            # Structure from debug: p_item['player']['team']['name']
            api_team = p_item.get("player", {}).get("team", {}).get("name", "")
            if fuzzy_match(team_name, api_team):
                team_players.append(p_item)
        
        if not team_players:
            return "No player trends found for this team."

        # Sort by pointsScored (PPG in this feed)
        team_players.sort(key=lambda x: -x.get("pointsScored", 0))
        
        trends = "Kilit Oyuncular: "
        for p in team_players[:3]:
            raw_name = p.get("player", {}).get("name", "Unknown")
            # Format "LAST, FIRST" to "First Last"
            if "," in raw_name:
                parts = raw_name.split(",")
                name = f"{parts[1].strip()} {parts[0].strip()}".title()
            else:
                name = raw_name.title()
                
            pts = p.get("pointsScored", 0)
            reb = p.get("totalRebounds", 0)
            ast = p.get("assists", 0)
            trends += f"{name} ({pts:.1f} PTS, {reb:.1f} REB, {ast:.1f} AST), "
            
        return trends.strip(", ")
    except Exception as e:
        logging.error(f"Error parsing Euroleague player trends: {e}")
        return ""
