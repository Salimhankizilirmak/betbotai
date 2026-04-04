import httpx
import pandas as pd
import os
import logging
import asyncio

DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

LEAGUE_URLS = {
    "EPL": "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
    "LA_LIGA": "https://www.football-data.co.uk/mmz4281/2526/SP1.csv",
    "BUNDESLIGA": "https://www.football-data.co.uk/mmz4281/2526/D1.csv",
    "SERIE_A": "https://www.football-data.co.uk/mmz4281/2526/I1.csv",
    "LIGUE_1": "https://www.football-data.co.uk/mmz4281/2526/F1.csv",
    "TURKEY": "https://www.football-data.co.uk/mmz4281/2526/T1.csv",
    "TURKEY_2": "https://www.football-data.co.uk/mmz4281/2526/T2.csv",
    "WORLD_CUP": "https://www.football-data.co.uk/new/world_cup.csv",
    "INT_QUAL": "https://www.football-data.co.uk/new/euro_qualifiers.csv"
}

# Per-league cache
_df_cache = {} 
_lock = asyncio.Lock()

async def get_dataframe(league="EPL"):
    """
    Belirli bir lig için verileri indirir ve DataFrame olarak döner.
    SSL bypass ve 3 deneme mekanizması içerir.
    """
    CSV_URL = LEAGUE_URLS.get(league)
    if not CSV_URL:
        logging.warning(f"No URL defined for league: {league}")
        return None
        
    CSV_PATH = os.path.join(DATA_DIR, f"{league}_historical.csv")
    
    async with _lock:
        if league in _df_cache:
            return _df_cache[league]
            
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
                        return None
                    await asyncio.sleep(2)

        try:
            df = pd.read_csv(CSV_PATH)
            _df_cache[league] = df
            return df
        except Exception as e:
            logging.error(f"Error reading CSV {CSV_PATH}: {e}")
            return None

async def get_team_stats(team_name, league_key="EPL"):
    """
    Belirli bir lig içindeki takımın performans özetini döner.
    """
    if not team_name:
        return ""
    
    df = await get_dataframe(league_key)
    if df is None:
        return f"{league_key} için tarihsel veri bulunamadı."
    
    try:
        # Takım ismini fuzzy (kelime bazlı) ara
        full_name = team_name.lower()
        first_word = team_name.split()[0].lower()
        
        # Öncelikli tam eşleşme veya ilk kelime eşleşmesi
        home_matches = df[
            (df['HomeTeam'].str.lower().str.contains(full_name, na=False)) | 
            (df['HomeTeam'].str.lower().str.contains(first_word, na=False))
        ]
        away_matches = df[
            (df['AwayTeam'].str.lower().str.contains(full_name, na=False)) | 
            (df['AwayTeam'].str.lower().str.contains(first_word, na=False))
        ]
        
        total_matches = len(home_matches) + len(away_matches)
        if total_matches == 0:
            return f"{team_name} için {league_key} verisi bulunamadı."
            
        # Bazı kolonlar her CSV'de olmayabilir (TUR2 gibi), kontrol et
        goals_scored = 0
        goals_conceded = 0
        if 'FTHG' in df.columns and 'FTAG' in df.columns:
            goals_scored = home_matches['FTHG'].sum() + away_matches['FTAG'].sum()
            goals_conceded = home_matches['FTAG'].sum() + away_matches['FTHG'].sum()
            
        corners = 0
        if 'HC' in df.columns and 'AC' in df.columns:
            corners = home_matches['HC'].sum() + away_matches['AC'].sum()
            
        yellow_cards = 0
        if 'HY' in df.columns and 'AY' in df.columns:
            yellow_cards = home_matches['HY'].sum() + away_matches['AY'].sum()
        
        avg_goals = round(goals_scored / total_matches, 2) if total_matches > 0 else 0
        avg_conceded = round(goals_conceded / total_matches, 2) if total_matches > 0 else 0
        avg_corners = round(corners / total_matches, 2) if total_matches > 0 else 0
        avg_yellows = round(yellow_cards / total_matches, 2) if total_matches > 0 else 0
        
        return f"{team_name} ({league_key} - Son {total_matches} Maç) - Atılan: {avg_goals}, Yenen: {avg_conceded}, Korner: {avg_corners}, Kart: {avg_yellows}"
    except Exception as e:
        return f"{league_key} veri okuma hatası: {e}"
