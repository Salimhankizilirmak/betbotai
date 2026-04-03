import asyncio
import os
import logging
from main import get_odds
from data_loader import get_team_stats

async def test_leagues():
    sports = [
        "soccer_epl",
        "soccer_spain_la_liga",
        "soccer_italy_serie_a",
        "soccer_germany_bundesliga",
        "soccer_france_ligue_one",
        "soccer_turkey_super_league",
        "soccer_uefa_champs_league",
        "basketball_nba",
        "basketball_euroleague"
    ]
    
    print("--- ODDS API TEST ---")
    for sport in sports:
        try:
            data = await get_odds(sport)
            count = len(data) if isinstance(data, list) else 0
            print(f"{sport}: {count} matches found.")
            if count > 0:
                match = data[0]
                home = match.get('home_team')
                print(f"  Example: {home} vs {match.get('away_team')}")
        except Exception as e:
            print(f"{sport}: ERROR - {e}")

    print("\n--- DATA LOADER TEST ---")
    # Test Turkey Stats
    tur_stats = await get_team_stats("Galatasaray", "TURKEY")
    print(f"TURKEY (Galatasaray): {tur_stats}")
    
    # Test La Liga Stats
    la_liga_stats = await get_team_stats("Real Madrid", "LA_LIGA")
    print(f"LA_LIGA (Real Madrid): {la_liga_stats}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_leagues())
