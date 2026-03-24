import sqlite3
import os
import logging
from datetime import datetime
import urllib.request
import urllib.parse

def send_telegram_message(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=payload)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logging.error(f"Telegram webhook failed: {e}")

DATABASE_FILE = os.path.join("data", "bets.db")
BET_AMOUNT = 100.0  # Sanal 100 BB

def init_db():
    try:
        if not os.path.exists("data"):
            os.makedirs("data")
        with sqlite3.connect(DATABASE_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            ''')
            
            # Migration check: Add bet_amount if it doesn't exist
            cursor.execute("PRAGMA table_info(bets)")
            columns = [column[1] for column in cursor.fetchall()]
            if 'bet_amount' not in columns:
                cursor.execute("ALTER TABLE bets ADD COLUMN bet_amount REAL DEFAULT 100.0")
                logging.info("Database migration: Added bet_amount column.")
                
            conn.commit()
    except Exception as e:
        logging.error(f"Database init error: {e}")

# Uygulama açılışında DB kontrolü/kurulumu
init_db()

def place_virtual_bet(event, ai_analysis, custom_amount=None):
    """
    Düşük riskli maçlar için sanal veritabanına bahis ekler.
    """
    amount = custom_amount if custom_amount else BET_AMOUNT
    match_id = event.get('id')
    
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
            cursor = conn.cursor()
            
            # Check if already bet
            cursor.execute('SELECT id FROM bets WHERE match_id = ?', (match_id,))
            if cursor.fetchone():
                return False
            
            # Place the bet
            commence_time = event.get('commence_time')
            home_team = event.get('home_team')
            away_team = event.get('away_team')
            sport_key = event.get('sport_key')
            bet_target = ai_analysis.get('bet_target')
            odds_value = ai_analysis.get('odds_value')
            risk_score = ai_analysis.get('risk_score', 50)
            
            cursor.execute('''
                INSERT INTO bets (match_id, sport_key, home_team, away_team, commence_time, risk_score, bet_target, odds_value, bet_amount, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            ''', (match_id, sport_key, home_team, away_team, commence_time, risk_score, bet_target, odds_value, amount))
            conn.commit()
            
            # Telegram bildirimi gönder
            msg = (
                f"🚨 <b>YENİ BAHİS ALINDI!</b>\n\n"
                f"🏀 <b>Maç:</b> {home_team} vs {away_team}\n"
                f"🎯 <b>Hedef:</b> {bet_target}\n"
                f"📈 <b>Oran:</b> {odds_value}\n"
                f"💰 <b>Miktar:</b> {amount} BB\n\n"
                f"🧠 <b>Baron'un Gerekçesi:</b>\n"
                f"<i>{ai_analysis.get('explanation', '')[:300]}...</i>"
            )
            send_telegram_message(msg)
            
            logging.info(f"VIRTUAL BET PLACED: {home_team} vs {away_team} | {bet_target} @ {odds_value} | Amount: {amount} BB")
            return True
    except Exception as e:
        logging.error(f"Error placing virtual bet: {e}")
        return False

def get_bet_history():
    """
    Grafik ve tablo için geçmiş bahisleri döner.
    """
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bets ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logging.error(f"History fetch error: {e}")
        return []

def get_recent_performance(limit=10):
    """
    AI'nın öğrenmesi için son maçların sonuçlarını özetler.
    """
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bets WHERE status != 'PENDING' ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            summary = []
            for r in rows:
                outcome = "KAZANDI" if r['status'] == 'WON' else "KAYBETTİ"
                summary.append(f"- {r['home_team']} vs {r['away_team']} | Hedef: {r['bet_target']} | Oran: {r['odds_value']} | SONUÇ: {outcome}")
            return "\n".join(summary) if summary else "Henüz sonuçlanmış bahis yok."
    except Exception as e:
        logging.error(f"Recent performance error: {e}")
        return "Performans verisi çekilemedi."

def resolve_bet_status(match_id, actual_result):
    """
    Bahis sonucunu günceller ve kar/zarar hesaplar.
    """
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT bet_target, odds_value, bet_amount, home_team, away_team FROM bets WHERE match_id = ? AND status = 'PENDING'", (match_id,))
            bet = cursor.fetchone()
            
            if not bet:
                return False
                
            prediction, odds, amount, home, away = bet
            is_winner = False
            
            # Basit kazanma mantığı (Geliştirilebilir)
            if prediction == actual_result:
                is_winner = True
            
            # Eğer API oran çekememişse (0.00) iflas etmemek için varsayılan 1.90 oran verelim
            safe_odds = odds if odds > 1.0 else 1.90
            
            status = 'WON' if is_winner else 'LOST'
            profit = (amount * safe_odds) - amount if is_winner else -amount
            
            cursor.execute("UPDATE bets SET status = ?, profit = ? WHERE match_id = ?", (status, profit, match_id))
            conn.commit()
            
            # Telegram bildirimi
            icon = "🟢" if is_winner else "🔴"
            msg = (
                f"{icon} <b>KUPON SONUÇLANDI!</b>\n\n"
                f"🏀 <b>Maç:</b> {home} vs {away}\n"
                f"🎯 <b>Alınan Hedef:</b> {prediction} @ {odds}\n"
                f"📊 <b>Durum:</b> {status}\n"
                f"💰 <b>Kâr/Zarar:</b> {'+' if is_winner else ''}{profit:.2f} BB"
            )
            send_telegram_message(msg)
            
            return True
    except Exception as e:
        logging.error(f"Bet resolution error for {match_id}: {e}")
        return False

def get_pending_sports():
    """
    Sonuç bekleyen spor dallarını benzersiz liste olarak döner.
    """
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT sport_key FROM bets WHERE status = 'PENDING'")
            return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logging.error(f"Pending sports fetch error: {e}")
        return []
