import httpx
import logging
import asyncio
from difflib import SequenceMatcher

# Premier League Pulselive API (v2/v3)
BASE_URL = "https://sdp-prem-prod.premier-league-prod.pulselive.com/api"
API_PLAYER_LEADERS = f"{BASE_URL}/v3/competitions/8/seasons/2025/players/stats/leaderboard"
API_TEAM_LEADERS = f"{BASE_URL}/v2/competitions/8/teams/stats/leaderboard"

# Caches
_pl_cache = {
    "goals": None,
    "assists": None,
    "clean_sheets": None,
    "team_goals": None,
    "team_clean_sheets": None,
    "last_updated": 0
}

async def fetch_pl_json(url: str, params: dict = None):
    """Generic async fetcher for PL Pulselive API."""
    headers = {
        "Origin": "https://www.premierleague.com",
        "Referer": "https://www.premierleague.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "x-pulse-application-version": "v1.42.13",
        "x-pulse-application-name": "web"
    }
    try:
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"PL API error ({response.status_code}) for {url}")
                return None
    except Exception as e:
        logging.error(f"Error fetching PL data: {e}")
        return None

async def refresh_pl_cache():
    """Refreshes PL leaderboard stats."""
    import time
    if time.time() - _pl_cache["last_updated"] < 14400: # 4 saatlik cache
        return

    logging.info("Refreshing Premier League Statistics Cache...")
    
    tasks = [
        fetch_pl_json(API_PLAYER_LEADERS, {"_sort": "goals:desc", "_limit": 100}),
        fetch_pl_json(API_PLAYER_LEADERS, {"_sort": "goal_assists:desc", "_limit": 100}),
        fetch_pl_json(API_TEAM_LEADERS, {"_sort": "goals:desc", "season": "2025", "_limit": 40}),
        fetch_pl_json(API_TEAM_LEADERS, {"_sort": "clean_sheets:desc", "season": "2025", "_limit": 40})
    ]
    
    results = await asyncio.gather(*tasks)
    
    if results[0]: _pl_cache["goals"] = results[0]
    if results[1]: _pl_cache["assists"] = results[1]
    if results[2]: _pl_cache["team_goals"] = results[2]
    if results[3]: _pl_cache["team_clean_sheets"] = results[3]
    _pl_cache["last_updated"] = time.time()
    
    logging.info("Premier League Cache Refreshed.")

def fuzzy_match(name1, name2, threshold=0.7):
    if not name1 or not name2: return False
    n1, n2 = name1.lower(), name2.lower()
    if n1 in n2 or n2 in n1: return True
    return SequenceMatcher(None, n1, n2).ratio() >= threshold

async def get_pl_team_stats(team_name: str):
    """Returns team goals and clean sheets summary."""
    await refresh_pl_cache()
    
    goals, cs = 0, 0
    found = False

    try:
        if _pl_cache["team_goals"] and "data" in _pl_cache["team_goals"]:
            for item in _pl_cache["team_goals"]["data"]:
                api_team = item.get("teamMetadata", {}).get("name", "")
                if fuzzy_match(team_name, api_team):
                    goals = item.get("stats", {}).get("goals", 0)
                    found = True
                    break
                    
        if _pl_cache["team_clean_sheets"] and "data" in _pl_cache["team_clean_sheets"]:
            for item in _pl_cache["team_clean_sheets"]["data"]:
                api_team = item.get("teamMetadata", {}).get("name", "")
                if fuzzy_match(team_name, api_team):
                    cs = item.get("stats", {}).get("cleanSheets", 0)
                    break
        
        if not found:
            return f"No PL stats found for {team_name}."
            
        return f"Premier League - Toplam Gol: {int(goals)}, Clean Sheet: {int(cs)}."
    except Exception as e:
        return f"Error parsing PL team stats: {e}"

async def get_pl_player_trends(team_name: str):
    """Returns top scorers and assisters for a team."""
    await refresh_pl_cache()
    
    scorers = []
    assisters = []

    try:
        # Find scorers for this team
        if _pl_cache["goals"] and "data" in _pl_cache["goals"]:
            for item in _pl_cache["goals"]["data"]:
                player_team = item.get("playerMetadata", {}).get("currentTeam", {}).get("name", "")
                if fuzzy_match(team_name, player_team):
                    name = item.get("playerMetadata", {}).get("name", "Unknown")
                    val = item.get("stats", {}).get("goals", 0)
                    scorers.append(f"{name} ({int(val)} Gol)")
                if len(scorers) >= 2: break

        # Find assisters
        if _pl_cache["assists"] and "data" in _pl_cache["assists"]:
            for item in _pl_cache["assists"]["data"]:
                player_team = item.get("playerMetadata", {}).get("currentTeam", {}).get("name", "")
                if fuzzy_match(team_name, player_team):
                    name = item.get("playerMetadata", {}).get("name", "Unknown")
                    val = item.get("stats", {}).get("goalAssists", 0)
                    assisters.append(f"{name} ({int(val)} Asist)")
                if len(assisters) >= 2: break
        
        res = "Öne Çıkanlar: " + ", ".join(scorers + assisters)
        return res if (scorers or assisters) else "Oyuncu verisi bulunamadı."
    except Exception as e:
        logging.error(f"Error parsing PL player trends: {e}")
        return ""
