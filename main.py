import os
import logging
import asyncio
import json
import time
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from oddsapi_client import get_odds, get_scores
from ai_analyzer import analyze_event, AI_CACHE, save_cache
from bet_manager import init_db, place_virtual_bet, get_bet_history, resolve_bet_status, get_pending_sports, check_bet_exists
from nba_player_props import analyze_nba_player_props, get_nba_event_props
from dotenv import load_dotenv

load_dotenv()

NBA_PROPS_CACHE = {"data": [], "last_updated": 0}

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
        "soccer_uefa_champs_league_women",
        "basketball_euroleague",
        "basketball_nba",
        "upcoming"
    ]
    
    all_raw = []
    for sport in sports_to_fetch:
        try:
            data = await get_odds(sport)
            if isinstance(data, list):
                all_raw.extend(data)
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
        soccer = await get_odds("soccer_uefa_champs_league_women")
        basketball = await get_odds("basketball_euroleague")
        nba = await get_odds("basketball_nba")
        upcoming = await get_odds("upcoming")
        
        all_odds = []
        for l in [soccer, basketball, nba, upcoming]:
            if isinstance(l, list): all_odds.extend(l)
            
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
                            
                            winner = "DRAW"
                            if h_score > a_score: winner = "HOME_WIN"
                            elif a_score > h_score: winner = "AWAY_WIN"
                            
                            await asyncio.to_thread(resolve_bet_status, event['id'], winner, h_score, a_score)
    except Exception as e:
        logging.error(f"Error in check_and_resolve_all_pending_bets: {e}")

async def background_resolver():
    logging.info("Background resolver started.")
    await asyncio.sleep(5)
    while True:
        await check_and_resolve_all_pending_bets()
        await asyncio.sleep(1800)

async def background_analyzer():
    logging.info("Background analyzer started.")
    await asyncio.sleep(15) # Başlangıçta 15 saniye bekle
    while True:
        try:
            # Önce sonuçları temizle ki bütçe güncellensin
            await check_and_resolve_all_pending_bets()
            
            soccer = await get_odds("soccer_uefa_champs_league_women")
            basket = await get_odds("basketball_euroleague")
            nba = await get_odds("basketball_nba")
            
            combined = []
            if isinstance(soccer, list): combined.extend(soccer[:10])
            if isinstance(basket, list): combined.extend(basket[:10])
            if isinstance(nba, list): combined.extend(nba[:10])
            
            # Zamanı en yakın olanları önce analiz yap ki dashboard hemen dolsun
            combined.sort(key=lambda x: x.get('commence_time', ''))
            
            count = 0
            for match in combined:
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
                    
                    if res and res.get("risk_score", 100) < 40 and res.get("odds_value", 0) >= 1.35:
                        import bet_manager
                        await asyncio.to_thread(bet_manager.place_virtual_bet, match, res)
                        count += 1
                        await asyncio.sleep(5)
                
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
                # İlk 6 yaklaşan maç için prop çek
                for event in events[:6]:
                    recs = await analyze_nba_player_props(
                        event["id"],
                        event.get("home_team", ""),
                        event.get("away_team", "")
                    )
                    all_recs.extend(recs)
                    await asyncio.sleep(2) # Nefes payı
                
                all_recs.sort(key=lambda x: -x["confidence"])
                NBA_PROPS_CACHE["data"] = all_recs
                NBA_PROPS_CACHE["last_updated"] = time.time()
                logging.info(f"Otonom NBA Props Cache Güncellendi: {len(all_recs)} patlama bulundu.")
        except Exception as e:
            logging.error(f"Background props analyzer error: {e}")
        
        await asyncio.sleep(7200) # 2 hours

@app.on_event("startup")
async def startup():
    init_db()
    # Sunucu açıldığında anında sonuçları kontrol et
    asyncio.create_task(check_and_resolve_all_pending_bets())
    
    asyncio.create_task(background_resolver())
    asyncio.create_task(background_analyzer())
    asyncio.create_task(background_props_analyzer())
    logging.info("BetBot Server Live on Port 8005")

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8005))
    uvicorn.run(app, host="0.0.0.0", port=port)
