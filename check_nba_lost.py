import psycopg2, os
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')

def check_recent_nba_bets():
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, match_id, home_team, away_team, status, commence_time, sport_key, created_at, bet_target 
            FROM bets 
            WHERE status = 'LOST' AND sport_key = 'basketball_nba'
            ORDER BY created_at DESC LIMIT 10;
        """)
        rows = cur.fetchall()
        for r in rows:
            print(f"ID: {r['id']} | MatchID: {r['match_id']} | Teams: {r['home_team']} vs {r['away_team']} | Target: {r['bet_target']} | Status: {r['status']} | Date: {r['commence_time']}")
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_recent_nba_bets()
