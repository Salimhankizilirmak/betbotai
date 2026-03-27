import os
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4"

# In-memory cache
CACHE = {}
CACHE_TTL = 1800 # 30 minutes in seconds

async def get_sports():
    cache_key = "sports"
    if cache_key in CACHE and time.time() - CACHE[cache_key]["timestamp"] < CACHE_TTL:
        print("Returning cached sports")
        return CACHE[cache_key]["data"]
        
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/sports", params={"apiKey": ODDS_API_KEY}, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        CACHE[cache_key] = {"data": data, "timestamp": time.time()}
        return data

async def get_odds(sport_key="upcoming", region="eu", markets="h2h,totals,spreads"):
    cache_key = f"odds_{sport_key}_{region}_{markets}"
    if cache_key in CACHE and time.time() - CACHE[cache_key]["timestamp"] < CACHE_TTL:
        print("Returning cached odds")
        return CACHE[cache_key]["data"]

    async with httpx.AsyncClient() as client:
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": region,
            "markets": markets,
            "oddsFormat": "decimal"
        }
        response = await client.get(f"{BASE_URL}/sports/{sport_key}/odds", params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        CACHE[cache_key] = {"data": data, "timestamp": time.time()}
        return data

async def get_scores(sport_key="upcoming", days_from=3):
    cache_key = f"scores_{sport_key}_{days_from}"
    if cache_key in CACHE and time.time() - CACHE[cache_key]["timestamp"] < CACHE_TTL:
        print("Returning cached scores")
        return CACHE[cache_key]["data"]

    async with httpx.AsyncClient() as client:
        params = {
            "apiKey": ODDS_API_KEY,
            "daysFrom": days_from
        }
        response = await client.get(f"{BASE_URL}/sports/{sport_key}/scores", params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        CACHE[cache_key] = {"data": data, "timestamp": time.time()}
        return data
