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
    Belirli bir lig için verileri indirir ve DataFrame olarak döner.
    SSL bypass ve 3 deneme mekanizması içerir.
    """
    global _download_failed
    CSV_URL = LEAGUE_URLS.get(league)
    CSV_PATH = os.path.join(DATA_DIR, f"{league}_historical.csv")
    
    async with _lock:
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
            
        if CSV_URL:
            if not os.path.exists(CSV_PATH):
                logging.info(f"Downloading historical data: {league} (Attempting SSL Bypass)...")
                for attempt in range(3):
                    try:
                        # verify=False ile SSL hataları aşılır, timeout artırıldı
                        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                            response = await client.get(CSV_URL)
                            if response.status_code == 200:
                                with open(CSV_PATH, 'wb') as f:
                                    f.write(response.content)
                                logging.info(f"Successfully downloaded: {league}")
                                break
                            else:
                                logging.warning(f"Download {league} failed with status {response.status_code}")
                    except Exception as e:
                        logging.error(f"Attempt {attempt+1} failed for {league}: {e}")
                        if attempt == 2:
                            _download_failed = True
                            return None
                        await asyncio.sleep(5)

            try:
                _df = pd.read_csv(CSV_PATH)
                return _df
            except Exception as e:
                logging.error(f"Error reading CSV {CSV_PATH}: {e}")
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
