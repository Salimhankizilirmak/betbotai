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
from oddsapi_client import get_odds, get_scores, warm_cache_from_shared
from ai_analyzer import analyze_event, AI_CACHE, NBA_PROPS_CACHE, reload_ai_cache, reload_nba_props
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
from leagues import ANALYZED_SPORTS
from dotenv import load_dotenv
import settings

# GLOBAL TIMEZONE SYNC
os.environ['TZ'] = 'Europe/Istanbul'
if hasattr(time, 'tzset'):
    time.tzset()

load_dotenv()


# Setup logging (Vercel'de yazılabilir yer sadece /tmp; bkz. settings.LOG_DIR)
LOG_FILE = os.path.join(settings.ensure_dir(settings.LOG_DIR), "betbot.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
# httpx her isteğin URL'sini (apiKey=... dahil) INFO seviyesinde loglar; anahtarlar loga düşmesin
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = FastAPI(title="BetBot AI")

# Panel oranları GitHub Actions işlerinin Supabase'e yazdığı ortak önbellekten okur; daha eski olsa da
# kullanılır ki her sayfa açılışı Odds API kredisi harcamasın
DASHBOARD_ODDS_MAX_AGE = 12 * 3600

# CORS AYARLARI (Cross-Origin Resource Sharing)
# Bu ayar, frontend veya dış sistemlerin (localhost:5173 vb.) API'ye erişebilmesini sağlar.
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files - Serve JS and CSS correctly
app.mount("/js", StaticFiles(directory="frontend/js"), name="js")
app.mount("/css", StaticFiles(directory="frontend/css"), name="css")

@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open(os.path.join("frontend", "index.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/odds/upcoming")
async def api_upcoming(recommended: bool = False, hours: int = 24):
    """
    Önümüzdeki `hours` saat (varsayılan 24) içinde başlayacak maçlar ve analizleri. recommended=true: sadece
    Pinnacle'a göre değer bulunanlar (value_vs_pinnacle); sanal bahis alındıysa gerçek stake ile.
    """
    from datetime import timezone, timedelta
    from bet_manager import get_db_connection
    await asyncio.to_thread(reload_ai_cache)
    await warm_cache_from_shared(DASHBOARD_ODDS_MAX_AGE)

    all_raw = []
    for sport in ANALYZED_SPORTS:
        try:
            data = await get_odds(sport, max_age=DASHBOARD_ODDS_MAX_AGE)
            if isinstance(data, list):
                all_raw.extend(data)
        except Exception as e:
            logging.error(f"Error fetching odds for {sport}: {e}")

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=max(1, min(hours, 24 * 14)))
    default_analysis = {"risk_score": 0, "win_probability": 0, "analysis": "Henüz analiz edilmedi.",
                        "bet_target": "N/A", "odds_value": 0.0, "is_recommended": False}
    seen, final_matches = set(), []
    for m in sorted(all_raw, key=lambda x: x.get("commence_time", "")):
        if not m.get("id") or m["id"] in seen:
            continue
        seen.add(m["id"])
        try:
            ct = datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ct <= now or ct > horizon:
            continue
        analysis = {**default_analysis, **AI_CACHE.get(m["id"], {})}
        if recommended:
            # Eski LLM analizleri (method yok) artık önerilmez; sadece değer yöntemi ve 24 saat penceresi
            if not (analysis.get("method") == "value_vs_pinnacle" and analysis.get("is_recommended")):
                continue
        match = {k: v for k, v in m.items() if k != "bookmakers"}  # panel oran listesini kullanmıyor (~1 MB)
        match["ai_analysis"] = analysis
        final_matches.append(match)

    if recommended and final_matches:
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT match_id, bet_amount, bet_target FROM bets WHERE match_id = ANY(%s)",
                            ([x["id"] for x in final_matches],))
                placed = {r["match_id"]: r for r in cur.fetchall()}
            for x in final_matches:
                b = placed.get(x["id"])
                x["ai_analysis"]["bet_placed"] = bool(b)
                x["ai_analysis"]["bet_amount"] = b["bet_amount"] if b else None
        except Exception as e:
            logging.error(f"bet lookup error: {e}")
    return final_matches

@app.api_route("/api/analyze/{event_id}", methods=["GET", "POST"])
async def api_analyze(event_id: str):
    """
    Belirli bir maç için on-demand AI analizi.
    """
    await asyncio.to_thread(reload_ai_cache)
    if event_id in AI_CACHE:
        cached = AI_CACHE[event_id]
        if cached.get("bet_target") != "N/A":
            return cached

    try:
        sports_to_check = ANALYZED_SPORTS
        await warm_cache_from_shared(DASHBOARD_ODDS_MAX_AGE)

        all_odds = []
        for sport in sports_to_check:
            data = await get_odds(sport, max_age=DASHBOARD_ODDS_MAX_AGE)
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
    await asyncio.to_thread(reload_nba_props)
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
    log_path = LOG_FILE
    if not os.path.exists(log_path):
        return {"logs": "> Sistem logu bekleniyor..."}
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            display = [l.strip() for l in lines if l.strip()][-20:]
        return {"logs": "<br>".join(display)}
    except:
        return {"logs": "Log okuma hatası."}

@app.get("/api/coupons")
def api_coupons(limit: int = 10):
    """Son kuponlar + sonuçlanan kuponların özeti (coupons tablosu)."""
    from bet_manager import get_db_connection
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM coupons ORDER BY coupon_date DESC, kind LIMIT %s", (min(max(limit, 1), 50),))
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT count(*) FILTER (WHERE status IN ('WON', 'LOST')) AS resolved,
                              count(*) FILTER (WHERE status = 'WON') AS won,
                              coalesce(sum(profit) FILTER (WHERE status IN ('WON', 'LOST')), 0) AS profit FROM coupons""")
        summary = dict(cur.fetchone())
    for r in rows:
        r["coupon_date"] = str(r["coupon_date"])
    return {"coupons": rows, "summary": summary}

@app.get("/api/health")
def health():
    from oddsapi_client import CREDIT_STATE
    return {"status": "ok", "port": 8005, "odds_api_credits": CREDIT_STATE}

@app.get("/api/stats")
async def api_stats():
    """
    Tüm sistem istatistiklerini ve son analizleri döner.
    """
    from bet_manager import get_structured_stats
    stats = get_structured_stats()
    await asyncio.to_thread(reload_ai_cache)
    await asyncio.to_thread(reload_nba_props)

    # Son 10 AI analizini cache'den çek
    latest_analyses = []
    # AI_CACHE'deki öğeleri zamana göre sırala
    sorted_cache = sorted(
        AI_CACHE.items(),
        key=lambda x: x[1].get("_cached_at", 0) if isinstance(x[1], dict) else 0,
        reverse=True
    )

    for event_id, analysis in sorted_cache[:15]:
        if isinstance(analysis, dict) and analysis.get("bet_target") != "N/A":
            # Maç detaylarını bulmaya çalış (Opsiyonel)
            latest_analyses.append({
                "event_id": event_id,
                **analysis
            })

    return {
        "performance": stats,
        "latest_analyses": latest_analyses,
        "nba_props": NBA_PROPS_CACHE.get("data", [])[:10],
        "server_time": datetime.now(IST).isoformat()
    }

async def check_and_resolve_all_pending_bets():
    """Bekleyen tüm bahislerin sonucunu kontrol eder ve sonuçlandırır."""
    try:
        pending_sports = await asyncio.to_thread(get_pending_sports)
        if pending_sports:
            logging.info(f"Checking results for pending sports: {pending_sports}")
            for sport in pending_sports:
                scores = await get_scores(sport) or []
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

                            # 1. Ana Maç Bahsini Çöz (Taraf/Alt-Üst)
                            await asyncio.to_thread(resolve_bet_status, event['id'], winner, h_score, a_score)

                            # 2. Varsa Bu Maçın Player Prop'larını Kontrol Et
                            import bet_manager
                            pending_bets = await asyncio.to_thread(bet_manager.get_bet_history)
                            # match_id PROP_match_id... şeklinde olanları da tetikle
                            pending_props = [b for b in pending_bets if b['status'] == 'PENDING' and b['match_id'].startswith(f"PROP_{event['id']}")]

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

        # 3. Geriye dönük revalidation (Hatalı sonuçları düzelt)
        await bet_manager.revalidate_resolved_bets()

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

async def run_resolve_cycle(revalidate=True):
    """Tek tur: bekleyen bahisleri sonuçlandırır, istenirse hatalı sonuçları yeniden kontrol eder."""
    logging.info("🔄 Bahis kontrolü başlatılıyor...")
    # NBA prop/skor sonuçları ESPN box score'larından okunur; önce son maçları biriktir
    import espn_nba
    await asyncio.to_thread(espn_nba.ingest_recent)
    await check_and_resolve_all_pending_bets()
    if revalidate:
        import bet_manager
        await bet_manager.revalidate_resolved_bets()

async def background_resolver():
    """Arka planda bahisleri periyodik olarak sonuçlandırır."""
    last_revalidation = 0
    while True:
        try:
            # Geriye dönük yanlış sonuçları da kontrol et (SAATLİK)
            now = time.time()
            do_revalidate = now - last_revalidation >= 3600
            await run_resolve_cycle(revalidate=do_revalidate)
            if do_revalidate:
                last_revalidation = now
            await asyncio.sleep(600)
        except Exception as e:
            logging.error(f"Error in background_resolver: {e}")
            await asyncio.sleep(60)

MAX_LLM_ANALYSES_PER_RUN = 15  # tur başına en fazla yeni LLM analizi (önbellekteki analizler sayılmaz)
EXPLAIN_VERSION = 3            # analiz istemi/veri bloğu değişince artır: eski analizler yeniden yazılır

async def run_value_cycle(events, max_bets=10, label="maç"):
    """
    Maç bahisleri (futbol + basketbol): Pinnacle marjsız olasılığı vs şirketler arası en iyi oran (value_bets.py).
    Model/LLM karar vermez; LLM sadece bahis alınan maçlar için Telegram/panel gerekçesi yazar.
    """
    import value_bets as vb
    import cache_store
    import match_context
    from ai_analyzer import explain_value_pick, AI_CACHE_NAMESPACE
    from datetime import timezone, timedelta
    now = datetime.now(timezone.utc)
    soon = now + timedelta(hours=24)
    evaluated, cache_items, placed, llm_calls = [], {}, 0, 0

    async def rich(e, res, analysis):
        """Gerçek veri bloğu + LLM analizi; aynı maç/seçim için 12 saat içindeki analiz yeniden kullanılır."""
        nonlocal llm_calls
        builder = match_context.football_context if e.get("sport_key", "").startswith("soccer") else match_context.basketball_context
        _, facts_text = await asyncio.to_thread(builder, e, res)
        prev = AI_CACHE.get(e["id"]) or {}
        explanation = None
        if (prev.get("explained_pick") == analysis["bet_target"] and prev.get("explain_version") == EXPLAIN_VERSION
                and time.time() - prev.get("explained_at", 0) < 12 * 3600):
            explanation = prev.get("explanation")
        elif llm_calls < MAX_LLM_ANALYSES_PER_RUN:
            llm_calls += 1
            explanation = await explain_value_pick(e, analysis, facts_text)
        out = vb.to_analysis(e, res, explanation, facts_text)
        if explanation:
            out["explained_pick"] = analysis["bet_target"]
            out["explain_version"] = EXPLAIN_VERSION
            out["explained_at"] = prev.get("explained_at") if explanation == prev.get("explanation") else time.time()
        return out

    for e in sorted(events, key=lambda x: x.get("commence_time", "")):
        try:
            ct = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
            if ct <= now:
                continue
            res = vb.evaluate_event(e)
            if not res:
                continue
            evaluated.append((e, res))
            analysis = vb.to_analysis(e, res)
            will_bet = bool(res["best"]) and placed < max_bets and not await asyncio.to_thread(check_bet_exists, e["id"])
            if res["best"] and (ct <= soon or will_bet):
                analysis = await rich(e, res, analysis)
            if will_bet:
                multiplier = await get_safe_mode_multiplier()
                stake = calculate_kelly_stake(analysis["odds_value"], analysis["win_probability"], get_current_balance())
                logging.info(f"✅ DEĞER BAHSİ ({label}): {e['home_team']} vs {e['away_team']} | {analysis['bet_target']} "
                             f"@ {analysis['odds_value']} ({analysis['bookmaker']}) EV {analysis['ev']:+.3f}")
                await asyncio.to_thread(place_virtual_bet, e, analysis, custom_amount=round(stake * multiplier, 2))
                placed += 1
            entry = {**analysis, "home_team": e.get("home_team"), "away_team": e.get("away_team"),
                     "sport_key": e.get("sport_key"), "commence_time": e.get("commence_time"), "_cached_at": time.time()}
            AI_CACHE[e["id"]] = entry
            cache_items[e["id"]] = entry
        except Exception as ex:
            logging.error(f"Değer turu hatası {e.get('id')}: {ex}")

    # 2. geçiş: 24 saat içindeki değer OLMAYAN maçlar da gerçek veri + (bütçe kalırsa) analiz alır;
    # analiz bahis önermez, değer olmadığını açıkça söyler
    for e, res in evaluated:
        if res["best"] or e["id"] not in cache_items:
            continue
        try:
            if datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) > soon:
                continue
            entry = {**cache_items[e["id"]], **(await rich(e, res, vb.to_analysis(e, res)))}
            AI_CACHE[e["id"]] = cache_items[e["id"]] = entry
        except Exception as ex:
            logging.error(f"Günlük analiz hatası {e.get('id')}: {ex}")
    await asyncio.to_thread(cache_store.put_many, AI_CACHE_NAMESPACE, cache_items)
    await asyncio.to_thread(vb.record_predictions, evaluated)
    n_value = sum(1 for _, r in evaluated if r["best"])
    logging.info(f"🎯 Değer turu ({label}): {len(events)} maç, {len(evaluated)} Pinnacle referanslı, "
                 f"{n_value} değerli, {placed} bahis, {llm_calls} yeni analiz.")
    return {"listed": len(events), "evaluated": len(evaluated), "value": n_value, "bets": placed, "analyses": llm_calls}

async def run_analysis_cycle(resolve_first=True):
    """
    Tek tur maç bahsi: futbol ve basketbol (taraf/toplam) için Pinnacle'a göre değer. Ek Odds API kredisi
    harcamaz (oran listeleri zaten çekiliyor/önbellekte). NBA oyuncu prop'ları ayrı turda (run_props_cycle).
    """
    # Önce sonuçları temizle ki bütçe güncellensin
    if resolve_first:
        await check_and_resolve_all_pending_bets()

    soccer, basketball = [], []
    for sport in ANALYZED_SPORTS:
        try:
            data = await get_odds(sport)
            if isinstance(data, list):
                (soccer if sport.startswith("soccer") else basketball).extend(data)
        except Exception as ex:
            logging.error(f"Error fetching {sport} for background analysis: {ex}")

    football = await run_value_cycle(soccer, max_bets=10, label="futbol")
    basket = await run_value_cycle(basketball, max_bets=5, label="basketbol")
    logging.info(f"Analiz turu bitti: futbol {football}, basketbol {basket}")
    return {"football": football, "basketball": basket}

async def background_analyzer():
    logging.info("Background analyzer started.")
    await asyncio.sleep(15) # Başlangıçta 15 saniye bekle
    while True:
        try:
            await run_analysis_cycle()
        except Exception as e:
            logging.error(f"Analyzer loop error: {e}")
        await asyncio.sleep(600) # Her 10 dakikada bir kontrol (3600'den düşürüldü)

async def run_props_cycle(resolve_first=True):
    """Tek tur NBA oyuncu prop analizi; sonuçları ortak önbelleğe yazar ve uygun prop'lara sanal bahis alır."""
    from ai_analyzer import save_nba_props
    import espn_nba
    # Oyuncu son maç verileri ESPN box score'larından (stats.nba.com bulut IP'lerini engelliyor)
    await asyncio.to_thread(espn_nba.ingest_recent)
    if resolve_first:
        await check_and_resolve_all_pending_bets()

    events = await get_odds("basketball_nba")
    if not (isinstance(events, list) and len(events) > 0):
        logging.info("NBA: yaklaşan maç yok.")
        return {"props": 0, "bets_placed": 0}

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

    all_recs.sort(key=lambda x: -x["edge"])
    await asyncio.to_thread(save_nba_props, all_recs)
    logging.info(f"Otonom NBA Props Cache Güncellendi: {len(all_recs)} edge'li prop bulundu.")

    # OTOMATİK PROP BAHİSİ: öneriler zaten model eşiklerini geçti (edge, beklenti, dakika, oran;
    # bkz. nba_player_props.passes_thresholds). Genel maç süzgeci (%65 olasılık) prop'lara uygulanmaz:
    # kalibre olasılıklarda ~1.85 orana %65 neredeyse hiç çıkmaz.
    prop_count = 0
    for prop in all_recs[:15]: # En yüksek edge'li 15 prop'u incele
        analysis = {
            "bet_target": prop["bet_target"],
            "odds_value": prop["odds"],
            "win_probability": prop["confidence"],
            "risk_score": 100 - prop["confidence"],
            "analysis": prop["reason"]
        }
        match_id = f"PROP_{prop['event_id']}_{prop['player']}_{prop['stat']}"
        exists = await asyncio.to_thread(check_bet_exists, match_id)
        if exists:
            continue

        logging.info(f"✅ PROP ONAYLANDI: {prop['bet_target']} @ {prop['odds']} | edge {prop['edge']:+.3f} ev {prop['ev']:+.3f}")

        # Güvenli Mod
        multiplier = await get_safe_mode_multiplier()

        # Çeyrek Kelly, modelin (kalibre) olasılığı ve seçilen tarafın oranıyla
        current_bal = get_current_balance()
        kelly_stake = calculate_kelly_stake(prop["odds"], prop["p_model"] * 100, current_bal)
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
    return {"props": len(all_recs), "bets_placed": prop_count}

async def background_props_analyzer():
    logging.info("Background NBA props analyzer started.")
    while True:
        try:
            await run_props_cycle()
        except Exception as e:
            logging.error(f"Background props analyzer error: {e}")

        await asyncio.sleep(7200) # 2 hours

async def background_odds_collector():
    """Başlamak üzere olan maçların kapanış oranlarını (CLV/backtest için) 5 dakikada bir toplar."""
    import strategy
    from ingest.odds_snapshots import collect_closing_lines
    if not strategy.COLLECT_CLOSING_LINES:
        logging.info("Kapanış oranı toplama kapalı (COLLECT_CLOSING_LINES=false).")
        return
    await asyncio.sleep(60)
    while True:
        try:
            await collect_closing_lines()
        except Exception as e:
            logging.error(f"Odds collector error: {e}")
        await asyncio.sleep(300)

@app.on_event("startup")
async def startup():
    # Vercel'de (serverless) döngü çalışmaz; zamanlanmış işler GitHub Actions'ta jobs.py ile koşar
    if not settings.RUN_BACKGROUND_LOOPS:
        logging.info("BetBot API live (background loops disabled; jobs run via GitHub Actions)")
        return
    init_db()
    # Sunucu açıldığında anında sonuçları kontrol et
    asyncio.create_task(check_and_resolve_all_pending_bets())

    asyncio.create_task(background_resolver())
    # 1. Startup'ta beklemede kalan dünkü/eski bahisleri çöz
    asyncio.create_task(emergency_resolve_stuck_bets())

    # 2. Döngüleri Başlat
    asyncio.create_task(background_analyzer())
    asyncio.create_task(background_props_analyzer())
    asyncio.create_task(background_odds_collector())
    logging.info("BetBot Server Live on Port 8005")

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8005))
    uvicorn.run(app, host="0.0.0.0", port=port)
