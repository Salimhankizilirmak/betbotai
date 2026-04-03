import os
import sys
import logging

logging.basicConfig(level=logging.INFO)

try:
    import bet_manager
    print("bet_manager imported successfully!")
    print("Testing DB connection and table existance...")
    
    with bet_manager.get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM bets")
            count = cursor.fetchone()['count']
            print(f"Connection OK! Table 'bets' has {count} rows.")
            
except Exception as e:
    print(f"Error: {e}")
