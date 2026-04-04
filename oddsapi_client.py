import os
import time
import httpx
import logging
from dotenv import load_dotenv
from api_key_manager import odds_api_manager

load_dotenv()

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
        max_retries = odds_api_manager.get_max_retries()
        for attempt in range(max_retries):
            api_key = odds_api_manager.get_current_key()
            response = await client.get(f"{BASE_URL}/sports", params={"apiKey": api_key}, timeout=10.0)
            
            if response.status_code in [401, 403, 429]:
                logging.warning(f"get_sports: API Key {api_key[:6]}... failed ({response.status_code}). Rotating...")
                odds_api_manager.rotate_key()
                time.sleep(1) # Small delay before retry
                continue
                
            response.raise_for_status()
            data = response.json()
            CACHE[cache_key] = {"data": data, "timestamp": time.time()}
            return data
        return None

async def get_odds(sport_key="upcoming", region="eu", markets="h2h,totals,spreads"):
    cache_key = f"odds_{sport_key}_{region}_{markets}"
    if cache_key in CACHE and time.time() - CACHE[cache_key]["timestamp"] < CACHE_TTL:
        print("Returning cached odds")
        return CACHE[cache_key]["data"]

    async with httpx.AsyncClient() as client:
        max_retries = odds_api_manager.get_max_retries()
        for attempt in range(max_retries):
            api_key = odds_api_manager.get_current_key()
            params = {
                "apiKey": api_key,
                "regions": region,
                "markets": markets,
                "oddsFormat": "decimal"
            }
            response = await client.get(f"{BASE_URL}/sports/{sport_key}/odds", params=params, timeout=10.0)
            
            if response.status_code in [401, 403, 429]:
                logging.warning(f"get_odds: API Key {api_key[:6]}... failed ({response.status_code}). Rotating...")
                odds_api_manager.rotate_key()
                time.sleep(1)
                continue
                
            response.raise_for_status()
            data = response.json()
            CACHE[cache_key] = {"data": data, "timestamp": time.time()}
            return data
        return None

async def get_scores(sport_key="upcoming", days_from=5):
    cache_key = f"scores_{sport_key}_{days_from}"
    if cache_key in CACHE and time.time() - CACHE[cache_key]["timestamp"] < CACHE_TTL:
        print("Returning cached scores")
        return CACHE[cache_key]["data"]

    async with httpx.AsyncClient() as client:
        max_retries = odds_api_manager.get_max_retries()
        for attempt in range(max_retries):
            api_key = odds_api_manager.get_current_key()
            params = {
                "apiKey": api_key,
                "daysFrom": days_from
            }
            response = await client.get(f"{BASE_URL}/sports/{sport_key}/scores", params=params, timeout=10.0)
            
            if response.status_code in [401, 403, 429]:
                logging.warning(f"get_scores: API Key {api_key[:6]}... failed ({response.status_code}). Rotating...")
                odds_api_manager.rotate_key()
                time.sleep(1)
                continue
                
            response.raise_for_status()
            data = response.json()
            CACHE[cache_key] = {"data": data, "timestamp": time.time()}
            return data
        return None
