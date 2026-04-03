import sqlite3
import json

conn = sqlite3.connect("bets.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) as c FROM bets")
total = cursor.fetchone()['c']

cursor.execute("SELECT * FROM bets ORDER BY created_at DESC LIMIT 10")
rows = cursor.fetchall()

output = {"total": total, "bets": [dict(r) for r in rows]}
with open("db_check_root.txt", "w") as f:
    f.write(json.dumps(output, indent=2))
