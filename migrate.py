import re

with open("bet_manager.py", "r", encoding="utf-8") as f:
    content = f.read()

# Imports
content = content.replace("import sqlite3", "import psycopg2\nfrom psycopg2.extras import RealDictCursor")

# DB File to URL
content = content.replace('DATABASE_FILE = os.path.join("data", "bets.db")', 'DATABASE_URL = os.getenv("DATABASE_URL")')

# init_db modifications
init_new = '''def init_db():
    try:
        with db_lock:
            with psycopg2.connect(DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS bets (
                            id SERIAL PRIMARY KEY,
                            match_id TEXT UNIQUE,
                            sport_key TEXT,
                            home_team TEXT,
                            away_team TEXT,
                            commence_time TEXT,
                            risk_score INTEGER,
                            bet_target TEXT,
                            odds_value REAL,
                            bet_amount REAL DEFAULT 100.0,
                            status TEXT DEFAULT 'PENDING',
                            profit REAL DEFAULT 0.0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    
                    # Migration check: Add bet_amount if it doesn't exist
                    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='bets'")
                    columns = [row[0] for row in cursor.fetchall()]
                    if 'bet_amount' not in columns:
                        cursor.execute("ALTER TABLE bets ADD COLUMN bet_amount REAL DEFAULT 100.0")
                        logging.info("Database migration: Added bet_amount column.")
                        
                    conn.commit()
    except Exception as e:
        logging.error(f"Database init error: {e}")'''

content = re.sub(r'def init_db\(\):.*?except Exception as e:.*?logging\.error\(f"Database init error: \{e\}"\)', init_new, content, flags=re.DOTALL)

# get_db_connection modifications
conn_new = '''def get_db_connection():
    """Güvenli DB bağlantısı döner."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn'''

content = re.sub(r'def get_db_connection\(\):.*?return conn', conn_new, content, flags=re.DOTALL)

# ? to %s replacement (only in cursor.execute lines to be safe)
content = content.replace('WHERE match_id = ?', 'WHERE match_id = %s')
content = content.replace('ORDER BY created_at DESC LIMIT ?', 'ORDER BY created_at DESC LIMIT %s')
content = content.replace('VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, \'PENDING\')', 'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, \'PENDING\')')

# Fix conn_local.execute in resolve_bet_status
fix_conn_execute = '''                    with get_db_connection() as conn_local:
                        cursor_local = conn_local.cursor()
                        cursor_local.execute("SELECT commence_time FROM bets WHERE match_id = %s", (match_id,))
                        row = cursor_local.fetchone()
                        commence_time = row['commence_time'] if row else None'''

content = re.sub(r'                    with get_db_connection\(\) as conn_local:.*?commence_time = row\[\'commence_time\'\] if row else None', fix_conn_execute, content, flags=re.DOTALL)

# Fix python psycopg2 connection context manager issues. In psycopg2, `with get_db_connection() as conn:` handles transactions (commit/rollback) but DOES NOT close the connection. We must add `conn.close()` or `contextlib.closing`.
# Let's write `with conn.cursor() as cursor:` and `conn.close()` at the end of blocks, or just use `psycopg2.pool` ?
# It's better to just write a safe connection manager.

safe_conn = '''import contextlib

@contextlib.contextmanager
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()'''

content = content.replace("import os", "import os\nimport contextlib")
content = re.sub(r'def get_db_connection\(\):.*?return conn', safe_conn, content, flags=re.DOTALL)

with open("bet_manager.py", "w", encoding="utf-8") as f:
    f.write(content)
