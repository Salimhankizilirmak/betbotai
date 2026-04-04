import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import logging
from bet_manager import resolve_bet_status, get_db_connection
from oddsapi_client import get_scores

load_dotenv()
logging.basicConfig(level=logging.INFO)

async def manual_resolve():
    print("--- MANUAL BET RESOLVER STARTING ---")
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT match_id, sport_key, home_team, away_team, status FROM bets WHERE status = 'PENDING'")
        pending_bets = cursor.fetchall()
        
    print(f"Found {len(pending_bets)} pending bets.")
    
    for bet in pending_bets:
        m_id = bet['match_id']
        sport = bet['sport_key']
        print(f"Resolving: {m_id} ({bet['home_team']} vs {bet['away_team']})")
        
        if m_id.startswith("PROP_"):
            # Resolve player prop
            success = resolve_bet_status(m_id, "MANUAL_NBA_API")
            print(f"  Prop Result: {'SUCCESS' if success else 'STILL PENDING (No check/not found)'}")
        else:
            # Resolve H2H/Total using Odds API Scores
            scores = await get_scores(sport)
            found = False
            for event in scores:
                if event['id'] == m_id and event.get('completed'):
                    # Logic similar to main.py
                    h_team = event.get('home_team')
                    s_list = event.get('scores', [])
                    if len(s_list) == 2:
                        s1, s2, n1 = s_list[0]['score'], s_list[1]['score'], s_list[0]['name']
                        h_score = int(s1) if n1 == h_team else int(s2)
                        a_score = int(s2) if n1 == h_team else int(s1)
                        winner = "DRAW"
                        if h_score > a_score: winner = "HOME_WIN"
                        elif a_score > h_score: winner = "AWAY_WIN"
                        
                        success = resolve_bet_status(m_id, winner, h_score, a_score)
                        print(f"  Match Result: {'SUCCESS - ' + winner if success else 'FAILED'}")
                        found = True
                        break
            if not found:
                print(f"  Match Result: Event not found or not completed in API (Odds-API)")

if __name__ == "__main__":
    import asyncio
    asyncio.run(manual_resolve())
