import os
import re
import json
import time
import asyncio
import httpx
import logging
from google import genai
from openai import OpenAI
from dotenv import load_dotenv

# Global AI Cache & NBA Prop Cache
AI_CACHE = {}
NBA_PROPS_CACHE = {"data": [], "last_updated": 0}

# İç bağımlılıklar
from data_loader import get_team_stats
from nba_data import get_nba_team_stats
from bet_manager import place_virtual_bet, get_recent_performance, get_performance_metrics
from nba_player_props import analyze_nba_player_props
from euroleague_data import get_euroleague_team_stats, get_euroleague_player_trends
from premier_league_data import get_pl_team_stats, get_pl_player_trends

load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Clients
# Clients
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={'api_version': 'v1beta'} # v1 yerine v1beta kullanarak 404'ü çözün
)
openai_client = OpenAI(api_key=OPENAI_API_KEY)
# Groq API'sini OpenAI kütüphanesi üzerinden çağırıyoruz (Daha stabil)
groq_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
) if GROQ_API_KEY else None

# Modeller
AI_MODELS = [
    'gemini-1.5-flash',
    'gemini-1.5-pro'
]
OPENAI_MODEL = "gpt-4o-mini"

# Cache Ayarları
CACHE_FILE = os.path.join("data", "ai_cache.json")
CACHE_TTL = 172800  # 48 hours for Free Tier stability

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading AI cache: {e}")
            return {}
    return {}

def save_cache(cache_data):
    try:
        if not os.path.exists("data"):
            os.makedirs("data")
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Failed to save AI cache: {e}")

AI_CACHE = load_cache()

def safe_int_extract(val, default=0):
    """Sözlük veya string olarak gelen sayısal değerleri güvenli bir şekilde int'e çevirir."""
    if val is None: return default
    if isinstance(val, dict):
        # {"value": 85}, {"result": 85}, {"decimal": 85} gibi her türlü yapıyı tara
        val = val.get("value", val.get("result", val.get("decimal", val.get("score", val.get("int", val.get("win_probability", default))))))
    
    if isinstance(val, str):
        # String içindeki ilk sayıyı bulmaya çalış (Örn: "%85" -> 85)
        match = re.search(r'(\d+)', val)
        if match: val = match.group(1)
        
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default

def is_match_analyzable(event):
    """Maçta h2h veya totals marketi olup olmadığını kontrol eder."""
    if not event.get("bookmakers"): return False
    for bkm in event["bookmakers"]:
        for mkt in bkm.get("markets", []):
            if mkt["key"] in ["h2h", "totals"]:
                return True
    return False

def extract_real_odds(event, bet_target):
    if not event.get("bookmakers"):
        return 0.0
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    try:
        for bkm in event["bookmakers"][:3]: # İlk 3 bahis şirketinden birini bul
            for market in bkm.get("markets", []):
                if market["key"] == "h2h" and bet_target in ["HOME_WIN", "AWAY_WIN", "DRAW"]:
                    target_name = ""
                    if bet_target == "HOME_WIN": target_name = home
                    elif bet_target == "AWAY_WIN": target_name = away
                    elif bet_target == "DRAW": target_name = "Draw"
                    
                    if target_name:
                        for out in market["outcomes"]:
                            if out["name"].lower() == target_name.lower():
                                return float(out["price"])
                
                elif market["key"] == "totals" and " " in bet_target:
                    # Bet target örn: "OVER 2.5", "UNDER 150.5"
                    parts = bet_target.upper().split()
                    direction = parts[0] # OVER / UNDER
                    try:
                        point = float(parts[1])
                        for out in market["outcomes"]:
                            if out["name"].upper() == direction and abs(float(out.get("point", 0)) - point) < 0.1:
                                return float(out["price"])
                    except Exception:
                        pass
    except Exception:
        pass
    return float(0.0)

async def retry_with_backoff(coro_func, *args, max_retries=3, initial_delay=5, **kwargs):
    """Üssel bekleme (Exponential Backoff) ile API çağrısını tekrar dener."""
    for attempt in range(max_retries):
        try:
            return await coro_func(*args, **kwargs)
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                delay = initial_delay * (2 ** attempt)
                logging.warning(f"Kota doldu (429), {delay} saniye bekleniyor... (Deneme {attempt+1})")
                await asyncio.sleep(delay)
            else:
                logging.error(f"API hatası: {e}")
                break
    return None

async def analyze_with_fallback(prompt):
    """Gemini fail olursa Groq veya OpenRouter ile analiz yapar. (TÜRKÇE)"""
    prompt = f"{prompt}\n\nLÜTFEN TÜRKÇE YANIT VER."
    # 1. Groq Denemesi
    if groq_client:
        try:
            logging.info("Gemini yetersiz, Groq (Llama 3.3) kullanılıyor...")
            completion = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}]
            )
            return completion.choices[0].message.content
        except Exception as e:
            logging.error(f"Groq Hatası: {e}")

    # 2. OpenRouter Denemesi
    if OPENROUTER_API_KEY:
        try:
            logging.info("Groq yetersiz, OpenRouter kullanılıyor...")
            async with httpx.AsyncClient() as httpx_client:
                response = await httpx_client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "mistralai/mistral-7b-instruct-v0.1",
                        "messages": [{"role": "user", "content": prompt}]
                    },
                    timeout=30.0
                )
                if response.status_code == 200:
                    data = response.json()
                    return data['choices'][0]['message']['content']
        except Exception as e:
            logging.error(f"OpenRouter Hatası: {e}")

    return "Alternatif modeller de başarısız oldu."

async def rule_based_analysis(match_data, home_stats, away_stats):
    """AI tamamen fail olursa istatistiklere dayalı basit kural motoru."""
    home_name = match_data.get('home_team', 'Ev')
    away_name = match_data.get('away_team', 'Deplasman')
    logging.info(f"AI başarısız, {home_name} vs {away_name} için Kural Tabanlı Karar kullanılıyor.")
    
    # Basit bir puanlama mantığı (Geliştirmeye açık)
    home_score = 50
    # İstatistiklerden anahtar kelime/değer yakalamaya çalış (data_loader çıktılarına göre)
    if "form: %" in str(home_stats).lower():
        # "form: %80" gibi bir yapı varsa
        matches = re.findall(r'%(\d+)', str(home_stats))
        if matches: home_score = int(matches[0])
    
    win_prob = safe_int_extract(home_score, 50)
    bet_target = "HOME_WIN" if win_prob > 55 else ("AWAY_WIN" if win_prob < 45 else "DRAW")
    
    return {
        "risk_score": 50,
        "win_probability": win_prob,
        "bet_target": bet_target,
        "odds_value": 0.0,
        "recommendation": "Kural Tabanlı Öneri",
        "analysis": f"AI modelleri yanıt vermediği için H2H ve form istatistiklerine dayalı otomatik karar verildi. (Tahmin: {win_prob}%)"
    }

async def analyze_with_openai(prompt):
    """OpenAI GPT-4o-mini ile analiz yapar."""
    try:
        response = await asyncio.to_thread(
            openai_client.chat.completions.create,
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"OpenAI error: {e}")
        return "OpenAI analizi başarısız oldu."

async def calculate_risk(match_data):
    """
    Gemini, OpenAI ve Fallback modelleri kullanarak işbirlikçi maç analizi yapar.
    Tüm adım Gemini hatalarına karşı global fallback içerir.
    """
    default_resp = {
        "risk_score": 99, 
        "win_probability": 0, 
        "bet_target": "N/A",
        "odds_value": 0.0,
        "recommendation": "Analiz Yapılamadı", 
        "analysis": "Yapay zeka modellerinin tamamı şu an kota limitinde veya hata verdi."
    }

    sport_key = match_data.get('sport_key', '')
    home_name = match_data.get('home_team', '')
    away_name = match_data.get('away_team', '')

    home_stats = None
    away_stats = None
    player_prop_trends = "Veri yok"
    
    if "soccer" in sport_key:
        if "premier_league" in sport_key:
            # Premier League İstatistik Entegrasyonu
            try:
                home_stats = await get_pl_team_stats(home_name)
                away_stats = await get_pl_team_stats(away_name)
                player_prop_trends = await get_pl_player_trends(home_name)
            except Exception as e:
                logging.error(f"Premier League stats error: {e}")
        else:
            # Diğer Soccer Ligleri (EPL/La Liga csv)
            home_stats = await get_team_stats(home_name)
            away_stats = await get_team_stats(away_name)
    elif "basketball" in sport_key:
        if "nba" in sport_key:
            home_stats = await asyncio.to_thread(get_nba_team_stats, home_name, away_name)
            away_stats = await asyncio.to_thread(get_nba_team_stats, away_name, home_name)
            # NBA Player Props Entegrasyonu
            try:
                event_id = match_data.get("id")
                props = await analyze_nba_player_props(event_id, home_name, away_name)
                if props:
                    # En iyi 5 prop trendini al
                    player_prop_trends = "\n".join([f"- {p['player']} ({p['stat']}): Hat {p['line']} | Güven: %{p['confidence']} | Gerekçe: {p['reason']}" for p in props[:5]])
            except Exception as e:
                logging.error(f"NBA Prop fetch error: {e}")
        elif "euroleague" in sport_key:
            # Euroleague İstatistik Entegrasyonu (Full Roster & Matchups)
            try:
                home_stats = await get_euroleague_team_stats(home_name)
                away_stats = await get_euroleague_team_stats(away_name)
                
                # Her iki takımın tam kadrosunu çek
                home_roster = await get_euroleague_roster(home_name)
                away_roster = await get_euroleague_roster(away_name)
                player_prop_trends = f"\nEŞLEŞMELER (MATCHUPS):\n{home_roster}\n{away_roster}"
            except Exception as e:
                logging.error(f"Euroleague stats error: {e}")

    past_performance = get_recent_performance(limit=10)
    ai_metrics = get_performance_metrics()

    # GLOBAL FALLBACK WRAPPER
    try:
        # 1. ADIM: Gemini İlk Analizi
        gemini_initial_prompt = (
            f"Sen 'Baron' adında, sadece verilere inanan uzman bir bahis stratejistisin. SADECE TÜRKÇE KONUŞ.\n"
            f"Şu maçı analiz et: {json.dumps(match_data, indent=2)}\n"
            f"TAKIM İSTATİSTİKLERİ: Ev: {home_stats} | Deplasman: {away_stats}\n"
            f"NBA OYUNCU TRENDLERİ: {player_prop_trends}\n"
            f"ÖNCEKİ BAHİSLERİN: {past_performance}\n"
            f"GENEL BAŞARI DURUMUN: {ai_metrics}\n\n"
            f"KESİN TALİMATLAR:\n"
            f"1. 'İyi kadro', 'Güçlü taraf' gibi genel ve boş övgüler KESİNLİKLE YASAKTIR. Sadece sağlanan TAKIM İSTATİSTİKLERİ'ndeki rakamlarla konuş.\n"
            f"2. Analizinde en az 2 adet spesifik istatistik (PTS, REB, AST, Form yüzdesi vb.) kullan. Eğer bu rakamlar analizinde yoksa o tahmini yapma.\n"
            f"3. BASKETBOL maçlarında 'DRAW' (Beraberlik) bahsini ASLA önerme.\n"
            f"4. EŞLEŞMELERİ (MATCHUPS) analiz et: Takımların kilit oyuncularını birbiriyle kıyasla. Örneğin; 'Home Team'in pivotu X, Away Team'in X oyuncusuna karşı ribaund üstünlüğü kurabilir' gibi derinlemesine yorumlar yap.\n"
            f"5. Eğer taraf bahsi oranları çok dengesizse (h2h < 1.30), mutlaka 'Over/Under' seçeneklerini değerlendir.\n"
            f"6. Başarı durumuna bakarak ekstra disiplinli ol.\n"
            f"Sadece derinlemesine analiz metni dön (JSON değil)."
        )
        
        gemini_analysis = ""
        for model_name in AI_MODELS:
            resp = await retry_with_backoff(client.aio.models.generate_content, model=model_name, contents=gemini_initial_prompt)
            if resp:
                gemini_analysis = resp.text
                break
        
        if not gemini_analysis:
            raise Exception("Gemini Analiz Yapamadı")

        await asyncio.sleep(5)

        # 2. ADIM: OpenAI Kritik (Fallback içerebilir)
        openai_prompt = f"Şu analizi eleştir: {gemini_analysis}\nMaç: {home_name} vs {away_name}"
        openai_critique = await analyze_with_openai(openai_prompt)
        if "başarısız oldu" in openai_critique:
            openai_critique = "OpenAI kotası bitti, direkt son karara geçiliyor."

        await asyncio.sleep(5)

        # 3. ADIM: Gemini Final Kararı
        final_prompt = (
            f"Analizleri sentezle ve final kararını RAW JSON olarak ver. SADECE TÜRKÇE ANALİZ YAZ.\n"
            f"İlk Analiz: {gemini_analysis}\nEleştiri: {openai_critique}\n\n"
            f"Analiz kısmında mutlaka sayısal verilere (Örn: 'Son 5 maçta 110 sayı ortalaması') yer ver.\n"
            f"SADECE ŞU JSON FORMATINI DÖN:\n"
            f"{{\"risk_score\": 0-100, \"win_probability\": 0-100, \"bet_target\": \"string\", \"odds_value\": float, \"recommendation\": \"string\", \"analysis\": \"Türkçe ve İstatistik Odaklı Analiz (Min 2 Rakam)\"}}"
        )
        
        final_result_text = ""
        for model_name in AI_MODELS:
            resp = await retry_with_backoff(client.aio.models.generate_content, model=model_name, contents=final_prompt)
            if resp:
                final_result_text = resp.text
                break
        
        if not final_result_text:
            raise Exception("Gemini Final Karar Veremedi")

        match_json = re.search(r'\{.*\}', final_result_text, re.DOTALL)
        if match_json:
            analysis = json.loads(match_json.group())
            # Sayısal değerleri sağlama al
            analysis["risk_score"] = safe_int_extract(analysis.get("risk_score"), 99)
            analysis["win_probability"] = safe_int_extract(analysis.get("win_probability"), 0)
            
            analysis["analysis"] = f"AI Sentez: {gemini_analysis[:100]}... | Sonuç: {analysis.get('analysis', '')}"
            real_odds = extract_real_odds(match_data, analysis.get("bet_target", ""))
            if real_odds > 1.0: analysis["odds_value"] = real_odds
            return analysis

    except Exception as e:
        logging.warning(f"AI Analiz Süreci Gemini ile Başarısız: {e}. Fallback (Groq/OpenRouter) deneniyor...")
        # Groq/OpenRouter üzerinden TÜM ANALİZİ TEK SEFERDE YAP
        ai_metrics = get_performance_metrics()
        fallback_prompt = (
            f"Sen 'Baron' stratejistisin. SADECE TÜRKÇE KONUŞ.\n"
            f"MAÇ: {home_name} vs {away_name}\nSTAT: {home_stats} vs {away_stats}\n"
            f"OYUNCU TRENDLERİ: {player_prop_trends}\n"
            f"Başarı Durumun: {ai_metrics}\n"
            f"İstatistik odaklı bir analiz yap (Analizde rakam kullanmak zorunludur) ve SADECE RAW JSON dön:\n"
            f"{{\"risk_score\": int, \"win_probability\": int, \"bet_target\": \"string\", \"odds_value\": float, \"recommendation\": \"string\", \"analysis\": \"Türkçe istatistik bazlı analiz\"}}"
        )
        fallback_text = await analyze_with_fallback(fallback_prompt)
        
        try:
            match_json = re.search(r'\{.*\}', fallback_text, re.DOTALL)
            if match_json:
                analysis = json.loads(match_json.group())
                # Sayısal değerleri sağlama al
                analysis["risk_score"] = safe_int_extract(analysis.get("risk_score"), 99)
                analysis["win_probability"] = safe_int_extract(analysis.get("win_probability"), 0)
                
                real_odds = extract_real_odds(match_data, analysis.get("bet_target", ""))
                if real_odds > 1.0: analysis["odds_value"] = real_odds
                return analysis
        except:
            pass
            
    # HİÇBİR ŞEY ÇALIŞMAZSA KURAL TABANLI MOD
    return await rule_based_analysis(match_data, home_stats, away_stats)


async def analyze_event(event):
    event_id = event.get("id")
    current_time = time.time()
    home_team = event.get("home_team")
    away_team = event.get("away_team")
    
    # 1. Kotayı korumak için odds filtreleme (1.50 - 2.50 aralığı "fırsat" maçlarıdır)
    # Bookmakers verisinden h2h oranlarını kontrol et
    best_odds = 0.0
    if event.get("bookmakers"):
        for bkm in event["bookmakers"]:
            for mkt in bkm.get("markets", []):
                if mkt["key"] == "h2h":
                    for out in mkt.get("outcomes", []):
                        best_odds = max(best_odds, out.get("price", 0))
    
    # Eğer oranlar hedef aralık dışında veya market yoksa default dön (h2h + totals desteği için is_match_analyzable kullanıyoruz)
    if not is_match_analyzable(event):
        logging.info(f"Skipping {home_team} vs {away_team}: No valid H2H or Totals markets found.")
        return default_resp
    
    # Not using legacy best_odds filter here anymore, trusting the analyzer or the should_we_bet logic later


    if event_id in AI_CACHE:
        cached_item = AI_CACHE[event_id]
        cache_ts = cached_item.get("_cached_at") or cached_item.get("timestamp", 0)
        if current_time - cache_ts < CACHE_TTL:
            if cached_item.get("bet_target") != "N/A":
                logging.info(f"Using valid cached analysis for {event.get('home_team')}")
                return cached_item

    analyzed_event = {
        "id": event_id,
        "sport_key": event.get("sport_key"),
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "commence_time": event.get("commence_time", ""),
        "bookmakers": event.get("bookmakers", [])[:1]
    }
    
    risk_info = await calculate_risk(analyzed_event)
    risk_score = risk_info.get("risk_score", 99)
    win_prob = risk_info.get("win_probability", 0)
    
    # Recommendation eşiğini test için biraz düşürelim
    risk_info["is_recommended"] = win_prob >= 50 and risk_score < 60
    
    stake_amount = 100
    if risk_score < 20:
        stake_amount = 500    # Çok yüksek güven — tam gaz
    elif risk_score < 35:
        stake_amount = 300    # Yüksek güven
    elif risk_score < 50:
        stake_amount = 200    # Orta-yüksek güven
    elif risk_score > 65:
        stake_amount = 50     # Düşük güven — öğrenme bahsi

    risk_info["bet_amount"] = stake_amount
    
    if risk_score != 99:
        AI_CACHE[event_id] = risk_info
        AI_CACHE[event_id]["_cached_at"] = time.time()
        save_cache(AI_CACHE)
        
    return risk_info

async def analyze_odds(odds_data):
    if not odds_data: return []
    results = []
    # Çoklu analiz sayısını 2'ye düşürerek kotayı koru
    for event in odds_data[:2]:
        res = await analyze_event(event)
        results.append(res)
        await asyncio.sleep(10) # Gemini 429 hatalarını azaltmak için bekleme ekle
    return results
