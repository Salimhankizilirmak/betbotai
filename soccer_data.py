import pandas as pd
import logging
from data_loader import get_dataframe

async def get_soccer_match_score(home_team, away_team, date_str, sport_key):
    """
    Yerel CSV dosyalarından (football-data.co.uk) futbol sonuçlarını çeker.
    """
    try:
        # Sport key -> League mapping
        mapping = {
            "soccer_turkey_super_league": "TURKEY",
            "soccer_turkey_1_league": "TURKEY_2",
            "soccer_epl": "EPL",
            "soccer_spain_la_liga": "LA_LIGA",
            "soccer_germany_bundesliga": "BUNDESLIGA",
            "soccer_italy_serie_a": "SERIE_A",
            "soccer_france_ligue_one": "LIGUE_1"
        }
        
        league = mapping.get(sport_key)
        if not league:
            return None
            
        df = await get_dataframe(league)
        if df is None:
            return None
            
        # Tarihi normalize et (CSV formatı genelde DD/MM/YY)
        # date_str: 2026-04-03 22:30:00
        from datetime import datetime
        try:
            target_dt = datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
            target_date_txt = target_dt.strftime('%d/%m/%y') # football-data.co.uk format
        except:
            return None
            
        # Filtrele
        # Not: Bazı CSV'lerde Date kolonu formatı değişebilir, birden fazla format dene
        possible_dates = [target_date_txt, target_dt.strftime('%d/%m/%Y')]
        
        # Takımları fuzzy bul
        from bet_manager import fuzzy_match
        
        # O günkü veya +/- 1 günkü maçlara bak (Saat farkı için)
        mask = df['Date'].isin(possible_dates)
        potential_games = df[mask]
        
        for _, row in potential_games.iterrows():
            if fuzzy_match(home_team, row['HomeTeam']) and fuzzy_match(away_team, row['AwayTeam']):
                h_score = int(row['FTHG'])
                a_score = int(row['FTAG'])
                logging.info(f"✅ SOCCER FALLBACK SUCCESS: {home_team} {h_score} - {a_score} {away_team} ({league})")
                return {
                    "home_score": h_score,
                    "away_score": a_score,
                    "completed": True
                }
                
        return None
    except Exception as e:
        logging.error(f"Error in soccer fallback: {e}")
        return None
