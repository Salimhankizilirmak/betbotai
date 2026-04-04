import os
import time
import logging
import asyncio
import json
import re
from datetime import datetime
import pytz
from fastapi import FastAPI, BackgroundTasks, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Global Timezone: Europe/Istanbul
IST = pytz.timezone('Europe/Istanbul')
from oddsapi_client import get_odds, get_scores
from ai_analyzer import analyze_event, AI_CACHE, NBA_PROPS_CACHE
from bet_manager import (
    init_db,
    place_virtual_bet, 
    get_pending_sports, 
    resolve_bet_status, 
    check_bet_exists,
    get_current_balance,
    calculate_kelly_stake,
    get_performance_metrics,
    fuzzy_match
)
from nba_player_props import analyze_nba_player_props
from dotenv import load_dotenv

# GLOBAL TIMEZONE SYNC
os.environ['TZ'] = 'Europe/Istanbul'
if hasattr(time, 'tzset'):
    time.tzset()

load_dotenv()


# Setup logging
if not os.path.exists("logs"):
    os.makedirs("logs")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(os.path.join("logs", "betbot.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

app = FastAPI(title="BetBot AI")

# Static files - Serve JS and CSS correctly
app.mount("/js", StaticFiles(directory="frontend/js"), name="js")
app.mount("/css", StaticFiles(directory="frontend/css"), name="css")

@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open(os.path.join("frontend", "index.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/odds/upcoming")
async def api_upcoming(recommended: bool = False):
    """
    Tüm yaklaşan maçları veya sadece önerilenleri döner.
    """
    sports_to_fetch = [
        "soccer_turkey_super_league",
        "soccer_epl", 
        "soccer_spain_la_liga", 
        "soccer_italy_serie_a", 
        "soccer_germany_bundesliga", 
        "soccer_france_ligue_one", 
        "soccer_uefa_champs_league", 
        "basketball_nba",
        "basketball_euroleague",
        "upcoming"
    ]
    
    all_raw = []
    for sport in sports_to_fetch:
        try:
            data = await get_odds(sport)
            if isinstance(data, list):
                all_raw.extend(data)
                logging.info(f"API: {sport} liginden {len(data)} maç çekildi.")
        except Exception as e:
            logging.error(f"Error fetching odds for {sport}: {e}")
            
    seen = set()
    unique_matches = []
    for m in all_raw:
        if m.get("id") and m["id"] not in seen:
            unique_matches.append(m)
            seen.add(m["id"])
            
    final_matches = []
    default_analysis = {
        "risk_score": 0, 
        "win_probability": 0, 
        "analysis": "Henüz analiz edilmedi.", 
        "bet_target": "N/A", 
        "odds_value": 0.0,
        "is_recommended": False,
        "bet_amount": 100
    }
    
    for match in unique_matches:
        analysis = AI_CACHE.get(match["id"], default_analysis)
        match["ai_analysis"] = {**default_analysis, **analysis}
        
        if recommended:
            if match["ai_analysis"].get("is_recommended"):
                final_matches.append(match)
        else:
            final_matches.append(match)
            
    return final_matches

@app.api_route("/api/analyze/{event_id}", methods=["GET", "POST"])
async def api_analyze(event_id: str):
    """
    Belirli bir maç için on-demand AI analizi.
    """
    if event_id in AI_CACHE:
        cached = AI_CACHE[event_id]
        if cached.get("bet_target") != "N/A":
            return cached
            
    try:
        sports_to_check = [
            "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
            "soccer_germany_bundesliga", "soccer_france_ligue_one",
            "soccer_turkey_super_league", "soccer_turkey_1_league",
            "soccer_uefa_champs_league", "basketball_nba",
            "basketball_euroleague", "upcoming"
        ]
        
        all_odds = []
        for sport in sports_to_check:
            data = await get_odds(sport)
            if isinstance(data, list):
                all_odds.extend(data)
            
        match_data = next((m for m in all_odds if m["id"] == event_id), None)
        
        if not match_data:
            return {"error": "Maç verisi bulunamadı. Lütfen sayfayı yenileyin."}
            
        result = await analyze_event(match_data)
        return result
    except Exception as e:
        logging.error(f"Analysis error: {e}")
        return {"error": str(e)}

@app.get("/api/nba/player-props")
async def api_player_props():
    """
    Döngüde arka planda toplanan NBA oyuncu prop analizlerini döner.
    Bu sayede frontend anında cevap alır ve timeout yaşamaz.
    """
    return NBA_PROPS_CACHE.get("data", [])

@app.get("/api/bets/history")
def api_bet_history():
    from bet_manager import get_current_balance, get_bet_history
    bets = get_bet_history()
    current_balance = get_current_balance()
    
    total_profit = sum(b.get("profit", 0) for b in bets if b.get("status") in ["WON", "LOST"])
    
    return {
        "current_balance": round(current_balance, 2),
        "profit": round(total_profit, 2),
        "bets": bets
    }

@app.get("/api/logs")
def get_logs():
    log_path = os.path.join(os.getcwd(), 'logs', 'betbot.log')
    if not os.path.exists(log_path):
        return {"logs": "> Sistem logu bekleniyor..."}
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            display = [l.strip() for l in lines if l.strip()][-20:]
        return {"logs": "<br>".join(display)}
    except:
        return {"logs": "Log okuma hatası."}

@app.get("/api/health")
def health():
    return {"status": "ok", "port": 8005}

async def check_and_resolve_all_pending_bets():
    """Bekleyen tüm bahislerin sonucunu kontrol eder ve sonuçlandırır."""
    try:
        pending_sports = await asyncio.to_thread(get_pending_sports)
        if pending_sports:
            logging.info(f"Checking results for pending sports: {pending_sports}")
            for sport in pending_sports:
                scores = await get_scores(sport)
                for event in scores:
                    if event.get('completed'):
                        home_team = event.get('home_team')
                        scores_list = event.get('scores', [])
                        if len(scores_list) == 2:
                            s1 = scores_list[0]['score']
                            s2 = scores_list[1]['score']
                            n1 = scores_list[0]['name']
                            
                            h_score = int(s1) if n1 == home_team else int(s2)
                            a_score = int(s2) if n1 == home_team else int(s1)
                            
                            # İSİM EŞLEŞTİRMESİNDE FUZZY MATCH KULLAN
                            # Eğer n1 vs home_team eşleşmezse fuzzy match ile kontrol et
                            from bet_manager import fuzzy_match
                            if not fuzzy_match(n1, home_team):
                                # Eğer n1 home_team değilse, n1 away_team mi diye bak
                                if fuzzy_match(n1, event.get('away_team', '')):
                                    h_score = int(s2)
                                    a_score = int(s1)
                            
                            winner = "DRAW"
                            if h_score > a_score: winner = "HOME_WIN"
                            elif a_score > h_score: winner = "AWAY_WIN"
                            
                            # 1. Ana Maç Bahsini Çöz (PENDING + son 3 günlük WON/LOST)
                            await asyncio.to_thread(resolve_bet_status, event['id'], winner, h_score, a_score)
                            
                            # 2. Varsa Bu Maçın Player Prop'larını Kontrol Et
                            import bet_manager
                            pending_props = [b for b in await asyncio.to_thread(bet_manager.get_bet_history) 
                                            if b['status'] == 'PENDING' and b['match_id'].startswith(f"PROP_{event['id']}")]
                            
                            for prop in pending_props:
                                await asyncio.to_thread(resolve_bet_status, prop['match_id'], "N/A")
    except Exception as e:
        logging.error(f"Error in check_and_resolve_all_pending_bets: {e}")

async def emergency_resolve_stuck_bets():
    """Startup'ta bekleyen tüm bahisleri (özellikle dünkü prop'ları) zorla çözmeye çalışır."""
    logging.info("🚀 EMERGENCY RESOLVER: Bekleyen bahisler taranıyor...")
    try:
        import bet_manager
        from oddsapi_client import get_scores
        
        # 1. Bekleyen tüm bahisleri çöz (H2H + PROP)
        await check_and_resolve_all_pending_bets()  # Argüman YOK
        
        # 2. Hala PENDING kalan PROP bahislerini zorla çözmeye çalış
        pending_bets = await asyncio.to_thread(bet_manager.get_bet_history)
        pending_props = [b for b in pending_bets if b['status'] == 'PENDING' and b['match_id'].startswith("PROP_")]
        
        if pending_props:
            logging.info(f"EMERGENCY: {len(pending_props)} bekleyen PROP bahsi NBA API ile çözülüyor...")
            for prop in pending_props:
                await asyncio.to_thread(resolve_bet_status, prop['match_id'], "STARTUP_RECOVERY")
        
        # 3. Yanlış sonuçlanan prop bahisleri arka planda düzelt (bildirim gönderme)
        await asyncio.to_thread(bet_manager.revalidate_resolved_bets)
                
    except Exception as e:
        logging.error(f"Error in emergency_resolve_stuck_bets: {e}")

async def get_safe_mode_multiplier():
    """Son performansa göre bahis miktar çarpanı döner (Bot zarardaysa miktar düşer)."""
    try:
        import bet_manager
        metrics = bet_manager.get_performance_metrics()
        # "Son 20 Maç Başarı Oranı: %45" gibi bir metinden oranı çek
        import re
        match = re.search(r'%(\d+)', metrics)
        if match:
            win_rate = int(match.group(1))
            if win_rate < 50:
                logging.warning(f"SİSTEM GÜVENLİ MODDA: Başarı oranı %{win_rate}. Miktarlar yarıya düşürüldü.")
                return 0.5
    except:
        pass
    return 1.0

def verify_and_place_bet(analysis, event):
    """
    SÜPER MASTER SÜZGEÇ (Sieve) Motoru
    1. Sayısal Kanıt: Analiz metninde en az 2 rakam/istatistik bulunmalı.
    2. Konsensüs (Olasılık) >= 65
    3. Risk Skoru < 35 (Sıkılaştırıldı)
    4. Oran Doğrulaması: 1.35 - 4.00
    """
    if not analysis or analysis.get("bet_target") == "N/A":
        return False, "Geçersiz analiz"
    
    import ai_analyzer
    import re
    
    analysis_text = analysis.get("analysis", "")
    prob = ai_analyzer.safe_int_extract(analysis.get("win_probability"), 0)
    risk = ai_analyzer.safe_int_extract(analysis.get("risk_score"), 100)
    odds = analysis.get("odds_value", 0.0)
    
    # 1. Filtre: Sayısal Kanıt Kontrolü (Min 2 Rakam/İstatistik)
    numbers = re.findall(r'\d+', analysis_text)
    if len(numbers) < 2:
        return False, f"Zayıf Analiz (Yetersiz Sayısal Veri: {len(numbers)} rakam)"

    # 2. Filtre: Basketbol Beraberlik Yasağı
    sport_key = event.get("sport_key", "").lower()
    if "basketball" in sport_key and analysis.get("bet_target") == "DRAW":
        return False, "Basketbolda beraberlik bahsi yasaktır."

    # 3. Filtre: Olasılık (AI Konsensüsü)
    if prob < 65:
        return False, f"Düşük Olasılık (%{prob})"
        
    # 4. Filtre: Risk Skoru
    if risk >= 35:
        return False, f"Yüksek Risk (Score: {risk})"
        
    # 4. Filtre: Oran Doğrulaması
    try:
        odds = float(odds)
    except:
        odds = 0.0
        
    if odds < 1.35 or odds > 4.00:
        return False, f"Oran Kapsam Dışı ({odds})"
        
    return True, "Master Süzgeçten Geçti"

async def background_resolver():
    """Arka planda bahisleri periyodik olarak sonuçlandırır."""
    while True:
        try:
            # Her 10 dakikada bir kontrol et
            logging.info("🔄 Arka plan bahis kontrolü başlatılıyor...")
            await check_and_resolve_all_pending_bets()
            await asyncio.sleep(600) 
        except Exception as e:
            logging.error(f"Error in background_resolver: {e}")
            await asyncio.sleep(60)

async def background_analyzer():
    logging.info("Background analyzer started.")
    await asyncio.sleep(15) # Başlangıçta 15 saniye bekle
    while True:
        try:
            # Önce sonuçları temizle ki bütçe güncellensin
            await check_and_resolve_all_pending_bets()
            
            sports_to_analyze = [
                "soccer_turkey_super_league",
                "soccer_turkey_1_league",
                "soccer_epl",
                "soccer_spain_la_liga",
                "soccer_italy_serie_a",
                "soccer_germany_bundesliga",
                "soccer_france_ligue_one",
                "soccer_uefa_champs_league",
                "basketball_nba",
                "basketball_euroleague"
            ]
            
            combined = []
            for sport in sports_to_analyze:
                try:
                    data = await get_odds(sport)
                    if isinstance(data, list):
                        combined.extend(data[:5]) # Her ligden en yakın 5 maçı al (Rate limit koruması)
                except Exception as ex:
                    logging.error(f"Error fetching {sport} for background analysis: {ex}")
            
            # Zamanı en yakın olanları önce analiz yap ki dashboard hemen dolsun
            combined.sort(key=lambda x: x.get('commence_time', ''))
            
            count = 0
            for match in combined:
                try:
                    match_id = match["id"]
                    exists = await asyncio.to_thread(check_bet_exists, match_id)
                    
                    if not exists:
                        res = None
                        if match_id in AI_CACHE:
                            # Eğer zaten analiz edildiyse cache'den al
                            res = AI_CACHE[match_id]
                            logging.info(f"Cached Analiz Kullanıldı: {match['home_team']} vs {match['away_team']}")
                        else:
                            # Henüz analiz edilmediyse AI'ya sor
                            logging.info(f"Otonom Analiz: {match['home_team']} vs {match['away_team']}")
                            res = await analyze_event(match)
                    
                        if not res: continue

                        # SÜPER MASTER SÜZGEÇ: Analizden Karara Geçiş
                        is_ok, reason = verify_and_place_bet(res, match)
                        
                        if is_ok:
                            logging.info(f"✅ BAHİS ONAYLANDI: {match['home_team']} vs {match['away_team']} | {reason}")
                            
                            # Güvenli Mod Çarpanı
                            multiplier = await get_safe_mode_multiplier()
                            
                            import bet_manager
                            current_bal = bet_manager.get_current_balance()
                            odds = res.get('odds_value', 1.90)
                            prob = res.get('win_probability', 50)
                            kelly_stake = bet_manager.calculate_kelly_stake(odds, prob, current_bal)
                            
                            final_stake = round(kelly_stake * multiplier, 2)
                            
                            # GÜVENLİ MOD: Eğer çarpan 0.5 ise (win rate < 50), miktar en fazla 25.0 olsun (Kullanıcı Talebi)
                            if multiplier < 1.0:
                                final_stake = 25.0
                            
                            await asyncio.to_thread(bet_manager.place_virtual_bet, match, res, custom_amount=final_stake)
                            count += 1
                            await asyncio.sleep(5)
                        else:
                            logging.info(f"❌ BAHİS REDDEDİLDİ: {match['home_team']} vs {match['away_team']} | {reason}")
                except Exception as match_error:
                    logging.error(f"Error processing match {match.get('id')}: {match_error}")
                    continue
                
                if count >= 10: break # Bir döngüde en fazla 10 bahis alsın
        except Exception as e:
            logging.error(f"Analyzer loop error: {e}")
        await asyncio.sleep(600) # Her 10 dakikada bir kontrol (3600'den düşürüldü)

async def background_props_analyzer():
    logging.info("Background NBA props analyzer started.")
    while True:
        try:
            await check_and_resolve_all_pending_bets()
            
            events = await get_odds("basketball_nba")
            if isinstance(events, list) and len(events) > 0:
                all_recs = []
                # Canlı maçları pas geç (Commence time kontrolü)
                from datetime import timezone
                now_utc = datetime.now(timezone.utc)
                valid_events = []
                for e in events:
                    if 'commence_time' in e:
                        try:
                            c_time = datetime.strptime(e['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                            if c_time > now_utc:
                                valid_events.append(e)
                        except:
                            pass # Parse hatası olursa riske atma, alma
                            
                for event in valid_events[:6]:
                    recs = await analyze_nba_player_props(event)
                    all_recs.extend(recs)
                    await asyncio.sleep(2) # Nefes payı
                
                all_recs.sort(key=lambda x: -x["confidence"])
                NBA_PROPS_CACHE["data"] = all_recs
                NBA_PROPS_CACHE["last_updated"] = time.time()
                logging.info(f"Otonom NBA Props Cache Güncellendi: {len(all_recs)} patlama bulundu.")
                
                # OTOMATİK PROP BAHİSİ (Süper Master Süzgeç Entegrasyonu)
                prop_count = 0
                for prop in all_recs[:15]: # En iyi 15 prop'u incele
                    # Prop için analiz nesnesi oluştur
                    analysis = {
                        "bet_target": prop["bet_target"],
                        "odds_value": prop["over_odds"],
                        "win_probability": prop["confidence"],
                        "risk_score": 100 - prop["confidence"], # Props için risk ters orantılı
                        "analysis": prop["reason"]
                    }
                    
                    # SÜZGEÇ KONTROLÜ
                    is_ok, reason = verify_and_place_bet(analysis, {"id": prop["event_id"]})
                    
                    if is_ok:
                        match_id = f"PROP_{prop['event_id']}_{prop['player']}_{prop['stat']}"
                        exists = await asyncio.to_thread(check_bet_exists, match_id)
                        
                        if not exists:
                            logging.info(f"✅ PROP ONAYLANDI: {prop['player']} {prop['stat']} | {reason}")
                            
                            # Güvenli Mod
                            multiplier = await get_safe_mode_multiplier()
                            
                            current_bal = get_current_balance()
                            kelly_stake = calculate_kelly_stake(prop["over_odds"], prop["confidence"], current_bal)
                            final_stake = round(kelly_stake * multiplier, 2)
                            
                            # Maç nesnesini taklit et
                            mock_match = {
                                "id": match_id,
                                "sport_key": "basketball_nba",
                                "home_team": prop["home_team"],
                                "away_team": prop["away_team"],
                                "commence_time": prop.get("commence_time", datetime.now(IST).isoformat())
                            }
                            
                            await asyncio.to_thread(place_virtual_bet, mock_match, analysis, custom_amount=final_stake)
                            prop_count += 1
                            if prop_count >= 5: break # Bir kerede max 5 prop bahsi
                    else:
                        if prop["confidence"] >= 75: # Sadece yüksek güvenli olanları logla
                            logging.info(f"❌ PROP REDDEDİLDİ: {prop['player']} {prop['stat']} | {reason}")

        except Exception as e:
            logging.error(f"Background props analyzer error: {e}")
        
        await asyncio.sleep(7200) # 2 hours

@app.on_event("startup")
async def startup():
    init_db()
    # Sunucu açıldığında anında sonuçları kontrol et
    asyncio.create_task(check_and_resolve_all_pending_bets())
    
    asyncio.create_task(background_resolver())
    # 1. Startup'ta beklemede kalan dünkü/eski bahisleri çöz
    asyncio.create_task(emergency_resolve_stuck_bets())
    
    # 2. Döngüleri Başlat
    asyncio.create_task(background_analyzer())
    asyncio.create_task(background_props_analyzer())
    logging.info("BetBot Server Live on Port 8005")

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8005))
    uvicorn.run(app, host="0.0.0.0", port=port)
