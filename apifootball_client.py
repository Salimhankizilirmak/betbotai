import httpx
import os
import logging
from dotenv import load_dotenv

load_dotenv()

# RapidAPI - API-Football Key (v3.football.api-sports.io)
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

async def get_team_squad(team_id):
    """
    Ücretsiz plana uygun sakatlık ve kadro bilgisini çeker.
    Günlük 100 istek limiti vardır.
    """
    if not API_FOOTBALL_KEY:
        logging.warning("API_FOOTBALL_KEY missing. Skipping squad data.")
        return []

    headers = {
        'x-rapidapi-key': API_FOOTBALL_KEY,
        'x-rapidapi-host': 'v3.football.api-sports.io'
    }
    
    async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
        try:
            # Oyuncu kadrosu ve sakatlık durumları için genel bir sorgu
            response = await client.get(f"{BASE_URL}/players/squads", params={"team": team_id}, headers=headers)
            if response.status_code == 200:
                return response.json().get("response", [])
            else:
                logging.error(f"API-Football error {response.status_code}: {response.text}")
                return []
        except Exception as e:
            logging.error(f"API-Football connection error: {e}")
            return []

async def get_team_id_by_name(name):
    """Takım ismine göre API-Football ID'sini bulur."""
    if not API_FOOTBALL_KEY: return None
    
    headers = {
        'x-rapidapi-key': API_FOOTBALL_KEY,
        'x-rapidapi-host': 'v3.football.api-sports.io'
    }
    async with httpx.AsyncClient(verify=False) as client:
        try:
            response = await client.get(f"{BASE_URL}/teams", params={"search": name}, headers=headers)
            if response.status_code == 200:
                teams = response.json().get("response", [])
                if teams:
                    return teams[0]["team"]["id"]
        except:
            pass
    return None
