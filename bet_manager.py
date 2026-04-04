import psycopg2
from psycopg2.extras import RealDictCursor
import os
import contextlib
import logging
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
import json
import time
from x_client import post_tweet

REVALIDATION_CACHE_FILE = os.path.join("data", "revalidation_status.json")


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
    Aynı match_id için birden fazla PENDING bahis (H2H, Alt/Üst vb.) olabilir.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # BU MAÇ İÇİN TÜM BEKLEYEN BAHİSLERİ ÇEK
            cursor.execute("SELECT id, bet_target, odds_value, bet_amount, home_team, away_team FROM bets WHERE match_id = %s AND status = 'PENDING'", (match_id,))
            pending_bets = cursor.fetchall()
            
            if not pending_bets:
                return False
            
            for bet in pending_bets:
                prediction = bet['bet_target']
                odds = bet['odds_value']
                amount = bet.get('bet_amount', 100.0)
                home = bet['home_team']
                away = bet['away_team']
                db_id = bet['id']
                is_winner = False
                status = 'LOST'
                profit = -amount
                
                if str(match_id).startswith("PROP_"):
                    # PROP RESOLUTION
                    target_str = str(prediction)
                    match_p = re.search(r'(.*?)\s*\|\s*(\w+)\s+(OVER|UNDER)\s+([\d.]+)', target_str)
                    if match_p:
                        prop_player = match_p.group(1).strip()
                        mkey_short = match_p.group(2).strip().upper()
                        direction = match_p.group(3).strip().upper()
                        target_line = float(match_p.group(4))
                        stat_map = {"PTS": "PTS", "REB": "REB", "AST": "AST", "POINTS": "PTS", "REBOUNDS": "REB", "ASSISTS": "AST"}
                        prop_stat = stat_map.get(mkey_short, "PTS")
                        
                        import nba_data
                        # Get commence_time
                        cursor.execute("SELECT commence_time FROM bets WHERE id = %s", (db_id,))
                        row = cursor.fetchone()
                        commence_time = row['commence_time'] if row else None
                        
                        if commence_time:
                            from datetime import datetime, timezone
                            try:
                                if hasattr(commence_time, 'tzinfo'):
                                    game_time = commence_time.replace(tzinfo=timezone.utc) if commence_time.tzinfo is None else commence_time
                                else:
                                    game_time = datetime.fromisoformat(str(commence_time).replace('Z', '+00:00'))
                            except:
                                game_time = None
                                
                            now_utc = datetime.now(timezone.utc)
                            if game_time and game_time > now_utc:
                                logging.info(f"Prop {match_id}: Maç henüz başlamadı ({game_time}). Atlanıyor.")
                                continue
                            
                            actual_val = nba_data.get_nba_player_game_stat(prop_player, str(commence_time), prop_stat)
                            if actual_val is not None:
                                if actual_val == -999.0: # DNP
                                    is_winner = False
                                    status = 'LOST'
                                    profit = 0.0
                                else:
                                    is_winner = actual_val > target_line if direction == "OVER" else actual_val < target_line
                                    status = 'WON' if is_winner else 'LOST'
                                    safe_odds = odds if odds > 1.0 else 1.90
                                    profit = (amount * safe_odds) - amount if is_winner else -amount
                            else:
                                continue # No data yet
                        else:
                            continue
                    else:
                        continue
                
                # 1. Taraf Bahsi Kontrolü (Kodlu: HOME_WIN, AWAY_WIN, DRAW)
                elif prediction in ["HOME_WIN", "AWAY_WIN", "DRAW"]:
                    if prediction == winner:
                        is_winner = True
                    elif winner not in ["HOME_WIN", "AWAY_WIN", "DRAW"]:
                        target_team = home if prediction == "HOME_WIN" else away
                        if fuzzy_match(target_team, winner):
                            is_winner = True
                    status = 'WON' if is_winner else 'LOST'
                    safe_odds = odds if odds > 1.0 else 1.90
                    profit = (amount * safe_odds) - amount if is_winner else -amount

                # 2. H2H Taraf Bahsi - Takım İsmiyle ("Team Name @ 1.50")
                elif " @ " in str(prediction) and ("OVER" not in str(prediction).upper() and "UNDER" not in str(prediction).upper()):
                    target_team = str(prediction).split(" @ ")[0].strip()
                    if winner == "HOME_WIN": is_winner = fuzzy_match(target_team, home)
                    elif winner == "AWAY_WIN": is_winner = fuzzy_match(target_team, away)
                    elif winner == "DRAW": is_winner = False
                    else: is_winner = fuzzy_match(target_team, winner)
                    status = 'WON' if is_winner else 'LOST'
                    safe_odds = odds if odds > 1.0 else 1.90
                    profit = (amount * safe_odds) - amount if is_winner else -amount

                # 3. Alt/Üst Bahsi Kontrolü
                elif h_score is not None and a_score is not None:
                    total_score = h_score + a_score
                    pred_upper = str(prediction).upper()
                    over_m = re.search(r'OVER\s+([\d.]+)', pred_upper)
                    under_m = re.search(r'UNDER\s+([\d.]+)', pred_upper)
                    if over_m:
                        is_winner = total_score > float(over_m.group(1))
                    elif under_m:
                        is_winner = total_score < float(under_m.group(1))
                    status = 'WON' if is_winner else 'LOST'
                    safe_odds = odds if odds > 1.0 else 1.90
                    profit = (amount * safe_odds) - amount if is_winner else -amount
                
                # Güncelle ve Bildir
                with db_lock:
                    cursor.execute("UPDATE bets SET status = %s, profit = %s WHERE id = %s", (status, profit, db_id))
                    conn.commit()
                
                icon = "🟢" if is_winner else ("⚪️" if profit == 0 else "🔴")
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

async def revalidate_resolved_bets():
    """
    Son 3 gün içinde sonuçlanmış bahisleri tekrar kontrol eder.
    Optimizasyon: 12 saatte bir kontrol, MS/Totals önceliği ve Timeout koruması.
    """
    logging.info("🔍 REVALIDATION: Akıllı kontrol başlatılıyor...")
    corrected = 0
    reverted = 0
    now = time.time()
    
    # Cache yükle
    reval_cache = {}
    if os.path.exists(REVALIDATION_CACHE_FILE):
        try:
            with open(REVALIDATION_CACHE_FILE, 'r') as f:
                reval_cache = json.load(f)
        except:
            pass

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, match_id, bet_target, odds_value, bet_amount, home_team, away_team, 
                       status, profit, commence_time, sport_key
                FROM bets 
                WHERE status IN ('WON', 'LOST')
                  AND commence_time IS NOT NULL 
                  AND CAST(commence_time AS TIMESTAMP) >= NOW() - INTERVAL '3 days'
            """)
            resolved_bets = cursor.fetchall()
            
            # 1. ÖNCELİKLENDİRME: Taraf ve Alt/Üst bahislerini başa al, Player Props'ları sona at
            resolved_bets.sort(key=lambda b: 1 if str(b['match_id']).startswith('PROP_') else 0)
            
            logging.info(f"REVALIDATION: Toplam {len(resolved_bets)} bahis taranacak...")
            
            import nba_data
            from oddsapi_client import get_scores
            score_cache = {}

            for bet in resolved_bets:
                db_id = str(bet['id'])
                match_id = bet['match_id']
                target_str = str(bet['bet_target'])
                commence_time = bet['commence_time']
                current_status = bet['status']
                sport = bet['sport_key']

                # 2. 12 SAATLİK BEKLEME KONTROLÜ
                last_check = reval_cache.get(db_id, 0)
                if now - last_check < 43200: # 12 saat
                    continue
                
                # logging.info(f"🔍 REVALIDATING: {target_str} (Status: {current_status}, MatchID: {match_id})")
                
                try:
                    from datetime import datetime, timezone
                    if hasattr(commence_time, 'tzinfo'):
                        game_time = commence_time if commence_time.tzinfo else commence_time.replace(tzinfo=timezone.utc)
                    else:
                        game_time = datetime.fromisoformat(str(commence_time).replace('Z', '+00:00'))
                    
                    if game_time > datetime.now(timezone.utc):
                        with db_lock:
                            cursor.execute("UPDATE bets SET status = 'PENDING', profit = 0.0 WHERE id = %s", (db_id,))
                            conn.commit()
                        reverted += 1
                        logging.warning(f"REVALIDATION REVERTED: Gelecek maç PENDING yapıldı: {match_id}")
                        reval_cache[db_id] = now
                        continue
                    
                    correct_winner_flag = False
                    found_data = False
                    actual_stat_text = ""
                    is_void = False

                    # 3. PROP BAHİS Mİ?
                    if str(match_id).startswith("PROP_"):
                        match_p = re.search(r'(.*?)\s*\|\s*(\w+)\s+(OVER|UNDER)\s+([\d.]+)', target_str)
                        if match_p:
                            p_name = match_p.group(1).strip()
                            m_key = match_p.group(2).strip().upper()
                            direction = match_p.group(3).strip().upper()
                            line = float(match_p.group(4))
                            stat_map = {"PTS": "PTS", "REB": "REB", "AST": "AST", "POINTS": "PTS", "REBOUNDS": "REB", "ASSISTS": "AST"}
                            
                            # NBA API Timeout Koruması
                            try:
                                actual_val = nba_data.get_nba_player_game_stat(p_name, str(commence_time), stat_map.get(m_key, "PTS"))
                            except Exception as api_err:
                                if "timeout" in str(api_err).lower():
                                    logging.warning(f"REVALIDATION TIMEOUT: {p_name} için NBA API zaman aşımı. 1 saat erteleniyor.")
                                    reval_cache[db_id] = now - 39600 # 12 saat - 1 saat = 11 saat (yani 1 saat sonra tekrar dener)
                                    continue
                                raise api_err

                            if actual_val is not None:
                                found_data = True
                                if actual_val == -999.0: # DNP
                                    correct_winner_flag = False
                                    is_void = True
                                    actual_stat_text = "DNP (Oynamadı)"
                                else:
                                    correct_winner_flag = actual_val > line if direction == "OVER" else actual_val < line
                                    is_void = False
                                    actual_stat_text = f"{actual_val} (Hat: {line})"
                    
                    # 4. NORMAL MAÇ BAHSİ (H2H / TOTALS)
                    else:
                        if sport not in score_cache:
                            try:
                                score_cache[sport] = await get_scores(sport)
                            except Exception as score_err:
                                logging.warning(f"get_scores failed for {sport}: {score_err}")
                                score_cache[sport] = None
                        
                        scores = score_cache.get(sport) or []
                        match_score = next((s for s in scores if s['id'] == match_id), None)
                        
                        fallback_score = None
                        # Odds API skoru yoksa VEYA skor var ama maç henüz "completed" değilse fallback dene
                        is_odds_completed = match_score.get('completed', False) if match_score else False
                        
                        if not is_odds_completed:
                            if sport == "basketball_nba":
                                logging.info(f"🏀 NBA FALLBACK TRIGGER: {match_id} için Odds API skoru yetersiz, NBA Stats kontrol ediliyor...")
                                fallback_score = nba_data.get_nba_match_score(bet['home_team'], bet['away_team'], commence_time)
                            elif "soccer" in sport:
                                try:
                                    import soccer_data
                                    logging.info(f"⚽ SOCCER FALLBACK TRIGGER: {match_id} için Yerel CSV kontrol ediliyor...")
                                    fallback_score = await soccer_data.get_soccer_match_score(bet['home_team'], bet['away_team'], commence_time, sport)
                                except: pass
                        
                        # Fallback skoru varsa onu kullan, yoksa Odds API skorunu kullan (eğer varsa)
                        final_res = fallback_score if fallback_score else match_score
                        
                        if final_res:
                            # Odds API 'completed' dönmeyebilir ama skorlar varsa kabul et
                            is_completed = final_res.get('completed', False) or ('scores' in final_res and len(final_res['scores']) == 2)
                            
                            if is_completed:
                                found_data = True
                                h_team = bet['home_team']
                                a_team = bet['away_team']
                                
                                # Veri yapısını normalize et
                                if 'scores' in final_res: # Odds API
                                    s_list = final_res['scores']
                                    if len(s_list) == 2:
                                        s1 = int(s_list[0]['score'])
                                        s2 = int(s_list[1]['score'])
                                        n1 = s_list[0]['name']
                                        hs = s1 if fuzzy_match(n1, h_team) else s2
                                        ascore = s2 if fuzzy_match(n1, h_team) else s1
                                    else:
                                        found_data = False
                                else: # Fallback (NBA/Soccer)
                                    hs = final_res['home_score']
                                    ascore = final_res['away_score']
                                
                                if found_data:
                                    # Winner Kodunu Belirle
                                    winner_code = "DRAW"
                                    if hs > ascore: winner_code = "HOME_WIN"
                                    elif ascore > hs: winner_code = "AWAY_WIN"
                                    
                                    prediction = target_str
                                    if prediction in ["HOME_WIN", "AWAY_WIN", "DRAW"]:
                                        correct_winner_flag = (prediction == winner_code)
                                    elif " @ " in prediction:
                                        t_team = prediction.split(" @ ")[0].strip()
                                        if winner_code == "HOME_WIN": correct_winner_flag = fuzzy_match(t_team, h_team)
                                        elif winner_code == "AWAY_WIN": correct_winner_flag = fuzzy_match(t_team, a_team)
                                        else: correct_winner_flag = False
                                    elif fuzzy_match(prediction, h_team) or fuzzy_match(prediction, a_team):
                                        # Prediction is a team name, check if they won
                                        if winner_code == "HOME_WIN": correct_winner_flag = fuzzy_match(prediction, h_team)
                                        elif winner_code == "AWAY_WIN": correct_winner_flag = fuzzy_match(prediction, a_team)
                                        else: correct_winner_flag = False
                                    else:
                                        # Totals check
                                        total = hs + ascore
                                        over_m = re.search(r'OVER\s+([\d.]+)', prediction.upper())
                                        under_m = re.search(r'UNDER\s+([\d.]+)', prediction.upper())
                                        if over_m: correct_winner_flag = total > float(over_m.group(1))
                                        elif under_m: correct_winner_flag = total < float(under_m.group(1))
                                    
                                    is_void = False
                                    actual_stat_text = f"{hs}-{ascore}"
                                else:
                                    found_data = False
                            else:
                                if not match_score: reval_cache[db_id] = now
                                continue
                        else:
                            if not match_score: reval_cache[db_id] = now
                            continue

                    # 5. Karşılaştır ve Düzelt
                    if found_data:
                        correct_status = 'WON' if correct_winner_flag else 'LOST'
                        is_winner = correct_winner_flag
                        odds = bet['odds_value']
                        amount = bet.get('bet_amount', 100.0)
                        safe_odds = odds if odds and odds > 1.0 else 1.90
                        
                        if is_void: # DNP durumu
                            new_profit = 0.0
                            correct_status = 'LOST' 
                        else:
                            new_profit = (amount * safe_odds) - amount if is_winner else -amount

                        if current_status != correct_status or abs(float(bet['profit']) - new_profit) > 0.01:
                            with db_lock:
                                cursor.execute("UPDATE bets SET status = %s, profit = %s WHERE id = %s", (correct_status, new_profit, db_id))
                                conn.commit()
                            corrected += 1
                            logging.info(f"✅ REVALIDATION FIXED: {target_str} | {current_status} → {correct_status}")
                            
                            icon = "🟢" if is_winner else ("⚪️" if is_void else "🔴")
                            msg = (
                                f"🔄 <b>DÜZELTME: KUPON GÜNCELLENDİ!</b>\n\n"
                                f"⚽️🏀 <b>Maç:</b> {bet['home_team']} vs {bet['away_team']}\n"
                                f"🎯 <b>Hedef:</b> {target_str}\n"
                                f"📊 <b>Gerçek:</b> {actual_stat_text}\n"
                                f"{icon} <b>Yeni Durum:</b> {correct_status}\n"
                                f"💰 <b>Kâr/Zarar:</b> {'+' if is_winner else ''}{new_profit:.2f} BB"
                            )
                            send_telegram_message(msg)
                        
                        # Her durumda cache güncelle
                        reval_cache[db_id] = now
                
                except Exception as inner_e:
                    logging.error(f"Revalidation inner error for {match_id}: {inner_e}")
                    continue
            
        # Cache'i kaydet
        with open(REVALIDATION_CACHE_FILE, 'w') as f:
            json.dump(reval_cache, f)

        logging.info(f"✅ REVALIDATION TAMAMLANDI: {corrected} düzeltildi, {reverted} PENDING geri alındı.")
    except Exception as e:
        logging.error(f"Revalidation error: {e}")
