import psycopg2
from psycopg2.extras import RealDictCursor
import os
import contextlib
import logging
from datetime import datetime
import urllib.request
import urllib.parse
import threading
from difflib import SequenceMatcher
from dotenv import load_dotenv
import re
from x_client import post_tweet

# Global DB Lock for writes
db_lock = threading.Lock()

load_dotenv()

def send_telegram_message(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids_str = os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_ids_str:
        return
    
    chat_ids = [c.strip() for c in chat_ids_str.split(",")]
    
    for chat_id in chat_ids:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }).encode("utf-8")
        try:
            logging.info(f"Sending Telegram message to {chat_id}...")
            req = urllib.request.Request(url, data=payload)
            with urllib.request.urlopen(req, timeout=10) as response:
                res_body = response.read().decode("utf-8")
                logging.info(f"Telegram response for {chat_id}: {res_body}")
        except Exception as e:
            logging.error(f"Telegram execution failed for {chat_id}: {e}")
            
    # X (Twitter) Gönderimi
    try:
        # Telegram HTML taglerini temizle (<b>, <i>, </b>, </i> vb.)
        clean_text = re.sub(r'<[^>]+>', '', text)
        
        # Twitter'a gönder (Ayrı bir thread'de çalıştırmak bloklamayı engeller)
        threading.Thread(target=post_tweet, args=(clean_text,), daemon=True).start()
    except Exception as e:
        logging.error(f"Failed to trigger X posting: {e}")

DATABASE_URL = os.getenv("DATABASE_URL")
BET_AMOUNT = 100.0  # Sanal 100 BB

def init_db():
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
        logging.error(f"Database init error: {e}")

# Uygulama açılışında DB kontrolü/kurulumu
init_db()

import contextlib

@contextlib.contextmanager
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

def get_performance_metrics():
    """
    AI'nın öğrenmesi için son 20 bahsin istatistiklerini hesaplar.
    Hangi liglerde başarılı/başarısız olduğumuzu döner.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Son 20 sonuçlanmış bahis
            cursor.execute("SELECT sport_key, status, odds_value FROM bets WHERE status != 'PENDING' ORDER BY created_at DESC LIMIT 20")
            rows = cursor.fetchall()
            
            if not rows:
                return "Henüz yeterli veri yok."
            
            total = len(rows)
            wins = sum(1 for r in rows if r['status'] == 'WON')
            win_rate = (wins / total) * 100
            
            # Lig bazlı başarı
            leagues = {}
            for r in rows:
                l = r['sport_key']
                if l not in leagues: leagues[l] = {"wins": 0, "total": 0}
                leagues[l]["total"] += 1
                if r['status'] == 'WON': leagues[l]["wins"] += 1
            
            league_stats = ", ".join([f"{l}: %{(s['wins']/s['total']*100):.0f}" for l, s in leagues.items()])
            
            # En çok kaybettiren ligi bul
            worst_league = "Yok"
            min_rate = 101
            for l, s in leagues.items():
                rate = (s['wins'] / s['total']) * 100
                if rate < min_rate:
                    min_rate = rate
                    worst_league = l
            
            return f"Başarı Oranı: %{win_rate:.0f}. En Başarısız Lig: {worst_league}. Lig Detayları: {league_stats}."
    except Exception as e:
        logging.error(f"Metrics error: {e}")
        return "Performans verisi çekilemedi."

def get_current_balance(start_balance=10000.0):
    """Veritabanındaki kâr/zarar durumuna göre güncel bakiyeyi hesaplar."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, profit, bet_amount FROM bets")
            rows = cursor.fetchall()
            
            total_profit = sum(row['profit'] for row in rows if row['status'] in ['WON', 'LOST'])
            pending_total = sum(row['bet_amount'] for row in rows if row['status'] == 'PENDING')
            
            return start_balance + total_profit - pending_total
    except Exception as e:
        logging.error(f"Balance calculation error: {e}")
        return start_balance

def calculate_kelly_stake(odds, probability, bankroll, fraction=0.25):
    """
    Fractional Kelly Criterion ile ideal bahis miktarını hesaplar.
    fraction: 0.25 (Çeyrek Kelly) olarak güncellendi.
    bankroll_cap: Kasanın maksimum %5'i tek bir bahse yatırılabilir.
    """
    if odds <= 1.0: return 25.0
    p = probability / 100.0
    q = 1.0 - p
    b = odds - 1.0
    
    # Kelly Formülü: f* = (bp - q) / b
    kelly_f = (b * p - q) / b
    
    if kelly_f <= 0: 
        return 25.0 # Minimum miktar
    
    recommended_stake = bankroll * kelly_f * fraction
    
    # %5 Bankroll Cap (Kasa koruma kilidi)
    max_allowed = bankroll * 0.05
    
    final_stake = min(recommended_stake, max_allowed)
    
    # Alt ve üst limitler: Min 25 BB, Max 1000 BB
    return round(max(25.0, min(final_stake, 1000.0)), 2)

def place_virtual_bet(event, ai_analysis, custom_amount=None):
    """
    Düşük riskli maçlar için sanal veritabanına bahis ekler.
    Kelly Criterion kullanarak dinamik miktar belirler.
    """
    match_id = event.get('id')
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Check if already bet
            cursor.execute('SELECT id FROM bets WHERE match_id = %s', (match_id,))
            if cursor.fetchone():
                return False
            
            # Dinamik miktar hesaplama
            if custom_amount:
                amount = custom_amount
            else:
                current_bal = get_current_balance()
                odds = ai_analysis.get('odds_value', 1.90)
                prob = ai_analysis.get('win_probability', 50)
                amount = calculate_kelly_stake(odds, prob, current_bal)
            
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING')
            ''', (match_id, sport_key, home_team, away_team, commence_time, risk_score, bet_target, odds_value, amount))
            
            with db_lock:
                conn.commit()
            
            # Telegram bildirimi gönder
            sport_icon = "⚽" if "soccer" in str(sport_key).lower() else "🏀"
            msg = (
                f"🚨 <b>YENİ BAHİS ALINDI (Çeyrek Kelly: %25)</b>\n\n"
                f"{sport_icon} <b>Maç:</b> {home_team} vs {away_team}\n"
                f"🎯 <b>Hedef:</b> {bet_target}\n"
                f"📈 <b>Oran:</b> {odds_value}\n"
                f"💰 <b>Miktar:</b> {amount} BB (Kasa: {get_current_balance():.2f})\n\n"
                f"🧠 <b>Baron'un Gerekçesi:</b>\n"
                f"<i>{ai_analysis.get('analysis', '')[:3800]}</i>"
            )
            send_telegram_message(msg)
            
            logging.info(f"VIRTUAL BET PLACED: {home_team} vs {away_team} | {bet_target} @ {odds_value} | Amount: {amount} BB (Kelly)")
            return True
    except Exception as e:
        logging.error(f"Error placing virtual bet: {e}")
        return False

def get_bet_history():
    """
    Grafik ve tablo için geçmiş bahisleri döner.
    """
    try:
        with get_db_connection() as conn:
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bets WHERE status != 'PENDING' ORDER BY created_at DESC LIMIT %s", (limit,))
            rows = cursor.fetchall()
            summary = []
            for r in rows:
                outcome = "KAZANDI" if r['status'] == 'WON' else "KAYBETTİ"
                summary.append(f"- {r['home_team']} vs {r['away_team']} | Hedef: {r['bet_target']} | Oran: {r['odds_value']} | SONUÇ: {outcome}")
            return "\n".join(summary) if summary else "Henüz sonuçlanmış bahis yok."
    except Exception as e:
        logging.error(f"Recent performance error: {e}")
        return "Performans verisi çekilemedi."

def check_bet_exists(match_id):
    """
    Belirli bir maç için bahis yapılıp yapılmadığını kontrol eder.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM bets WHERE match_id = %s", (match_id,))
            return cursor.fetchone() is not None
    except:
        return False

def fuzzy_match(name1, name2, threshold=0.7):
    """İki takım isminin benzerliğini kontrol eder (Lakers vs LA Lakers)."""
    if not name1 or not name2: return False
    n1, n2 = str(name1).lower().strip(), str(name2).lower().strip()
    
    # Direkt içerme kontrolü (Fenerbahçe Beko -> Fenerbahçe)
    if n1 in n2 or n2 in n1: return True
    
    # Kelime bazlı kontrol (Örn: "LA Lakers" ve "Lakers" için "Lakers" ortak)
    words1 = set(n1.split())
    words2 = set(n2.split())
    common = words1.intersection(words2)
    if common and any(len(w) > 3 for w in common): # Kısa kelimeler (FC, SK vb.) hariç
        return True

    return SequenceMatcher(None, n1, n2).ratio() >= threshold

def resolve_bet_status(match_id, winner, h_score=None, a_score=None):
    """
    Bahis sonucunu günceller ve kar/zarar hesaplar.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT bet_target, odds_value, bet_amount, home_team, away_team FROM bets WHERE match_id = %s AND status = 'PENDING'", (match_id,))
            bet = cursor.fetchone()
            
            if not bet:
                return False
                
            prediction = bet['bet_target']
            odds = bet['odds_value']
            amount = bet.get('bet_amount', 100.0)
            home = bet['home_team']
            away = bet['away_team']
            is_winner = False
            
            if str(match_id).startswith("PROP_"):
                # PROP RESOLUTION IMPROVEMENT: Parse player name from bet_target (SQL)
                # bet_target format: "Carlton Carrington | REB OVER 3.5"
                import re
                target_str = str(prediction) # bet_target from SQL
                match_p = re.search(r'(.*?)\s*\|\s*(\w+)\s+(OVER|UNDER)\s+([\d.]+)', target_str)
                
                if match_p:
                    prop_player = match_p.group(1).strip()
                    mkey_short = match_p.group(2).strip().upper() # PTS, REB, AST
                    direction = match_p.group(3).strip().upper()
                    target_line = float(match_p.group(4))
                    
                    # Convert short stat to NBA API stat
                    stat_map = {"PTS": "PTS", "REB": "REB", "AST": "AST", "POINTS": "PTS", "REBOUNDS": "REB", "ASSISTS": "AST"}
                    prop_stat = stat_map.get(mkey_short, "PTS")
                    
                    import nba_data
                    # Get commence_time from the bet record
                    with get_db_connection() as conn_local:
                        cursor_local = conn_local.cursor()
                        cursor_local.execute("SELECT commence_time FROM bets WHERE match_id = %s", (match_id,))
                        row = cursor_local.fetchone()
                        commence_time = row['commence_time'] if row else None
                    
                    if commence_time:
                        from datetime import datetime, timezone, timedelta
                        # Parse commence_time (str veya datetime olabilir)
                        try:
                            if hasattr(commence_time, 'tzinfo'):
                                game_time = commence_time.replace(tzinfo=timezone.utc) if commence_time.tzinfo is None else commence_time
                            else:
                                game_time = datetime.fromisoformat(str(commence_time).replace('Z', '+00:00'))
                        except:
                            game_time = None
                        
                        # MAÇ HENUZ OYNAMADI KONTROLU: commence_time gelecekteyse atla!
                        now_utc = datetime.now(timezone.utc)
                        if game_time and game_time > now_utc:
                            logging.info(f"Prop {match_id}: Maç henüz başlatmadı (Başlamasi: {game_time}). Atlanıyor.")
                            return False
                        
                        actual_val = nba_data.get_nba_player_game_stat(prop_player, str(commence_time), prop_stat)
                        
                        if actual_val is not None:
                            # VOID/DNP check: If game finished and player has no stats, mark as void
                            if actual_val == -999.0:
                                logging.info(f"DNP DETECTED: {prop_player} didn't play. Voiding bet.")
                                is_winner = False
                                safe_odds = 1.0 # Void odds
                                h_score, a_score = 0, 0
                            else:
                                if direction == "OVER":
                                    is_winner = actual_val > target_line
                                else:
                                    is_winner = actual_val < target_line
                                
                                h_score, a_score = actual_val, target_line
                                logging.info(f"PROP RESOLVED: {prop_player} {prop_stat} Actual={actual_val} vs Line={target_line} | Win={is_winner}")
                            
                            status = 'WON' if is_winner else 'LOST'
                            # For DNP, force profit to 0.0
                            if actual_val == -999.0:
                                profit = 0.0
                                status = 'LOST' # Mark as LOST but with 0 profit (VOID)
                            else:
                                safe_odds = odds if odds > 1.0 else 1.90
                                profit = (amount * safe_odds) - amount if is_winner else -amount
                        else:
                            logging.info(f"Prop {match_id} için henüz boxscore verisi yok (NBA API). Atlanıyor.")
                            return False
                    else:
                        logging.warning(f"Prop {match_id} için veritabanında commence_time bulunamadı.")
                        return False
                else:
                    logging.error(f"Prop parsing error: bet_target format invalid: {target_str}")
                    return False
            
            # 1. Taraf Bahsi Kontrolü (HOME_WIN, AWAY_WIN, DRAW)
            elif prediction in ["HOME_WIN", "AWAY_WIN", "DRAW"]:
                if prediction == winner:
                    is_winner = True
                elif winner not in ["HOME_WIN", "AWAY_WIN", "DRAW"]:
                    # Eğer winner dize olarak gelmişse (takım ismi) fuzzy match yap
                    target_team = home if prediction == "HOME_WIN" else away
                    if fuzzy_match(target_team, winner):
                        is_winner = True
                    else:
                        logging.info(f"Fuzzy match failed: {target_team} vs {winner}")
                else:
                    logging.info(f"Prediction {prediction} did not match winner {winner}")
            
            # 2. Alt/Üst Bahsi Kontrolü (OVER 2.5, UNDER 210.5 vb.)
            elif h_score is not None and a_score is not None:
                total_score = h_score + a_score
                parts = prediction.split()
                if len(parts) >= 2:
                    direction = parts[0].upper() # OVER / UNDER
                    try:
                        point = float(parts[1])
                        if direction == "OVER" and total_score > point:
                            is_winner = True
                        elif direction == "UNDER" and total_score < point:
                            is_winner = True
                        else:
                            logging.info(f"Total {total_score} did not satisfy {prediction}")
                    except Exception as e:
                        logging.error(f"Point parsing error for {prediction}: {e}")
                else:
                    logging.warning(f"Prediction format invalid for score check: {prediction}")
            else:
                logging.warning(f"Bet {match_id} could not be resolved: Stats/Winner missing. Predicted={prediction}, winner={winner}, h_score={h_score}")
                return False
            
            # PROP bahisler için status/profit zaten yukarıda set edildi
            if not str(match_id).startswith("PROP_"):
                # Eğer API oran çekememişse (0.00) iflas etmemek için varsayılan 1.90 oran verelim
                safe_odds = odds if odds > 1.0 else 1.90
                status = 'WON' if is_winner else 'LOST'
                profit = (amount * safe_odds) - amount if is_winner else -amount
            
            with db_lock:
                cursor.execute("UPDATE bets SET status = %s, profit = %s WHERE match_id = %s", (status, profit, match_id))
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT sport_key FROM bets WHERE status = 'PENDING'")
            return [row['sport_key'] for row in cursor.fetchall()]
    except Exception as e:
        logging.error(f"Pending sports fetch error: {e}")
        return []

def revalidate_resolved_bets():
    """
    Son 2 günde yanlış sonuçlanan PROP bahislerini NBA API'den tekrar doğrular ve düzeltir.
    - Gelecek maçlar PENDING'e geri alınır
    - Yanlış WON/LOST tespitler düzeltilir
    - Mevcut yapıyı bozmaz, yalnızca kayıtları günceller
    """
    import nba_data
    from datetime import datetime, timezone
    
    logging.info("🔍 REVALIDATION: Çözümlenen prop bahisleri tekrar doğrulanıyor...")
    corrected = 0
    reverted = 0
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Son 2 günlük çözümlenmiş PROP bahislerini al
            cursor.execute("""
                SELECT match_id, bet_target, odds_value, bet_amount, home_team, away_team, 
                       status, profit, commence_time
                FROM bets 
                WHERE status IN ('WON', 'LOST')
                  AND match_id LIKE 'PROP_%%'
                  AND commence_time >= NOW() - INTERVAL '3 days'
            """)
            resolved_props = cursor.fetchall()
            logging.info(f"REVALIDATION: {len(resolved_props)} çözümlenmiş prop bahisi kontrol ediliyor...")
            
            for bet in resolved_props:
                match_id = bet['match_id']
                target_str = str(bet['bet_target'])
                commence_time = bet['commence_time']
                current_status = bet['status']
                
                try:
                    # 1. Gelecek maç kontrolü: Bu maç hiç oynandı mı?
                    if commence_time:
                        if hasattr(commence_time, 'tzinfo'):
                            game_time = commence_time if commence_time.tzinfo else commence_time.replace(tzinfo=timezone.utc)
                        else:
                            game_time = datetime.fromisoformat(str(commence_time).replace('Z', '+00:00'))
                        
                        if game_time > datetime.now(timezone.utc):
                            # Bu maç henüz oynamadı, PENDING'e geri al
                            with db_lock:
                                cursor.execute("UPDATE bets SET status = 'PENDING', profit = 0.0 WHERE match_id = %s", (match_id,))
                                conn.commit()
                            reverted += 1
                            logging.warning(f"REVALIDATION REVERTED: Oynamayan maç PENDING'e döndürüldü: {match_id}")
                            continue
                    
                    # 2. bet_target parse et
                    match_p = re.search(r'(.*?)\s*\|\s*(\w+)\s+(OVER|UNDER)\s+([\d.]+)', target_str)
                    if not match_p:
                        continue
                    
                    prop_player = match_p.group(1).strip()
                    mkey_short = match_p.group(2).strip().upper()
                    direction = match_p.group(3).strip().upper()
                    target_line = float(match_p.group(4))
                    stat_map = {"PTS": "PTS", "REB": "REB", "AST": "AST", "POINTS": "PTS", "REBOUNDS": "REB", "ASSISTS": "AST"}
                    prop_stat = stat_map.get(mkey_short, "PTS")
                    
                    # 3. Gerçek istatistiği çek
                    actual_val = nba_data.get_nba_player_game_stat(prop_player, str(commence_time), prop_stat)
                    
                    if actual_val is None or actual_val == -999.0:
                        continue  # Veri yok, geç
                    
                    # 4. Doğru sonucu hesapla
                    correct_winner = actual_val > target_line if direction == "OVER" else actual_val < target_line
                    correct_status = 'WON' if correct_winner else 'LOST'
                    
                    if current_status != correct_status:
                        odds = bet['odds_value']
                        amount = bet.get('bet_amount', 100.0)
                        safe_odds = odds if odds and odds > 1.0 else 1.90
                        new_profit = (amount * safe_odds) - amount if correct_winner else -amount
                        
                        with db_lock:
                            cursor.execute(
                                "UPDATE bets SET status = %s, profit = %s WHERE match_id = %s",
                                (correct_status, new_profit, match_id)
                            )
                            conn.commit()
                        corrected += 1
                        logging.info(f"✅ REVALIDATION FIXED: {prop_player} {prop_stat} Actual={actual_val} vs Line={target_line} | {current_status} → {correct_status} ({'+' if correct_winner else ''}{new_profit:.2f} BB)")
                        
                        # Telegram bildirimi: Hata düzeltildi
                        icon = "🟢" if correct_winner else "🔴"
                        msg = (
                            f"🔄 <b>DÜZELTME: KUPON GÜNCELLENDI!</b>\n\n"
                            f"🏀 <b>Hedef:</b> {target_str}\n"
                            f"📊 <b>Gerçek:</b> {prop_stat} = {actual_val} (Hat: {target_line})\n"
                            f"{icon} <b>Yeni Durum:</b> {correct_status}\n"
                            f"💰 <b>Kâr/Zarar:</b> {'+' if correct_winner else ''}{new_profit:.2f} BB"
                        )
                        send_telegram_message(msg)
                        
                except Exception as inner_e:
                    logging.error(f"Revalidation inner error for {match_id}: {inner_e}")
                    continue
            
        logging.info(f"✅ REVALIDATION TAMAMLANDI: {corrected} düzeltildi, {reverted} PENDING'e geri alındı.")
    except Exception as e:
        logging.error(f"Revalidation error: {e}")

