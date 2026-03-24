import httpx
import pandas as pd
import os
import logging
import asyncio

DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

LEAGUE_URLS = {
    "EPL": "https://www.football-data.co.uk/mmz4281/2324/E0.csv",
}

_df = None
_lock = asyncio.Lock()
_download_failed = False

async def get_dataframe(league="EPL"):
    """
    Downloads and loads historical data for a given league.
    """
    global _df, _download_failed
    
    if _download_failed and not os.path.exists(os.path.join(DATA_DIR, f"{league}.csv")):
        return None

    CSV_URL = LEAGUE_URLS.get(league)
    CSV_PATH = os.path.join(DATA_DIR, f"{league}.csv")

    async with _lock:
        if _df is not None:
            return _df
            
        if CSV_URL:
            if not os.path.exists(CSV_PATH):
                try:
                    logging.info(f"Downloading historical data from {CSV_URL}...")
                    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                        response = await client.get(CSV_URL)
                        response.raise_for_status()
                        with open(CSV_PATH, 'wb') as f:
                            f.write(response.content)
                    logging.info(f"Successfully downloaded: {league}")
                except Exception as e:
                    logging.error(f"Failed to download data: {e}")
                    _download_failed = True
                    return None

            try:
                _df = pd.read_csv(CSV_PATH)
                return _df
            except Exception as e:
                logging.error(f"Error reading CSV: {e}")
                _download_failed = True
                return None
    return None

async def get_team_stats(team_name):
    """
    Returns a string summary of team performance from the dataframe.
    """
    if not team_name:
        return ""
    
    df = await get_dataframe()
    if df is None:
        return "Tarihsel veri yok."
    
    try:
        # Team search with fuzzy match (first word)
        first_word = team_name.split()[0]
        home_matches = df[df['HomeTeam'].str.contains(first_word, case=False, na=False)]
        away_matches = df[df['AwayTeam'].str.contains(first_word, case=False, na=False)]
        
        total_matches = len(home_matches) + len(away_matches)
        if total_matches == 0:
            return "İstatistik bulunamadı."
            
        goals_scored = home_matches['FTHG'].sum() + away_matches['FTAG'].sum()
        goals_conceded = home_matches['FTAG'].sum() + away_matches['FTHG'].sum()
        corners = home_matches['HC'].sum() + away_matches['AC'].sum()
        yellow_cards = home_matches['HY'].sum() + away_matches['AY'].sum()
        
        avg_goals = round(goals_scored / total_matches, 2)
        avg_conceded = round(goals_conceded / total_matches, 2)
        avg_corners = round(corners / total_matches, 2)
        avg_yellows = round(yellow_cards / total_matches, 2)
        
        return f"{team_name} (Son {total_matches} Maç) - Atılan: {avg_goals}, Yenen: {avg_conceded}, Korner: {avg_corners}, Kart: {avg_yellows}"
    except Exception as e:
        return f"Veri okuma hatası: {e}"
