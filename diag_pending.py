import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import logging
from bet_manager import resolve_bet_status, get_db_connection

load_dotenv()
logging.basicConfig(level=logging.INFO)

def diag_bets():
    print("--- DIAGNOSTIC START ---")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, match_id, sport_key, home_team, away_team, status, commence_time FROM bets WHERE status = 'PENDING'")
            pending = cursor.fetchall()
            print(f"Total Pending: {len(pending)}")
            
            for bet in pending[:10]:
                print(f"ID: {bet['id']} | Match: {bet['home_team']} vs {bet['away_team']} | Sport: {bet['sport_key']} | Time: {bet['commence_time']}")
                
                if bet['match_id'].startswith("PROP_"):
                    print("  Attempting prop resolution...")
                    # This will call nba_api
                    success = resolve_bet_status(bet['match_id'], "DIAG_RECOVERY")
                    print(f"  Resolution Attempt: {'SUCCESS' if success else 'FAILED/WAITING'}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    diag_bets()
