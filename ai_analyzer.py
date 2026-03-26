import os
import json
import asyncio
import logging
import time
import re
import httpx
from google import genai
from openai import OpenAI
from groq import Groq
from dotenv import load_dotenv

# İç bağımlılıklar
from data_loader import get_team_stats
from nba_data import get_nba_team_stats
from bet_manager import place_virtual_bet, get_recent_performance

load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Clients
client = genai.Client(api_key=GEMINI_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Modeller
AI_MODELS = [
    'gemini-1.5-flash',
    'gemini-1.5-pro',
    'gemini-2.0-flash'
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

def extract_real_odds(event, bet_target):
    if not event.get("bookmakers"):
        return 0.0
    home = event.get("home_team")
    away = event.get("away_team")
    try:
        for bkm in event["bookmakers"][:3]: # İlk 3 bahis şirketinden birini bul
            for market in bkm.get("markets", []):
                if market["key"] == "h2h" and bet_target in ["HOME_WIN", "AWAY_WIN"]:
                    target_name = home if bet_target == "HOME_WIN" else away
                    for out in market["outcomes"]:
                        if out["name"] == target_name:
                            return float(out["price"])
                elif market["key"] == "totals":
                    # Örn: "OVER 2.5" veya "UNDER 210.5"
                    parts = bet_target.split()
                    if len(parts) >= 2:
                        direction = parts[0].upper() # OVER / UNDER
                        try:
                            target_point = float(parts[1])
                        except:
                            target_point = None
                            
                        for out in market["outcomes"]:
                            out_name = out["name"].upper() # Over / Under
                            out_point = out.get("point")
                            
                            if out_name == direction:
                                # Eğer AI bir barem belirttiyse ona en yakın olanı seç
                                if target_point is not None:
                                    if abs(out_point - target_point) < 0.1:
                                        return float(out["price"])
                                else:
                                    # Barem belirtilmediyse ilk baremi al
                                    return float(out["price"])
    except Exception:
        pass
    return 0.0

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

    home_stats = ""
    away_stats = ""
    
    if "soccer" in sport_key.lower():
        home_stats = await get_team_stats(home_name)
        away_stats = await get_team_stats(away_name)
    
    if "basketball_nba" in sport_key:
        home_stats = await asyncio.to_thread(get_nba_team_stats, home_name, away_name)
        away_stats = await asyncio.to_thread(get_nba_team_stats, away_name, home_name)

    past_performance = get_recent_performance(limit=10)

    # 1. ADIM: Gemini İlk Analizi
    gemini_initial_prompt = f"""
Uzman bir spor analisti olarak şu maçı detaylıca analiz et:
{json.dumps(match_data, indent=2)}
İstatistikler:
Ev: {home_stats}
Deplasman: {away_stats}
Geçmiş Performansımız: {past_performance}

Analizinde sakatlıklar, form durumu ve değerli bahis seçeneklerini değerlendir. Sadece analiz metni dön.
"""
    
    gemini_analysis = ""
    for model_name in AI_MODELS:
        resp = await retry_with_backoff(client.aio.models.generate_content, model=model_name, contents=gemini_initial_prompt)
        if resp:
            gemini_analysis = resp.text
            break
    
    # Eğer Gemini tamamen fail olursa fallback kullan
    if not gemini_analysis:
        gemini_analysis = await analyze_with_fallback(gemini_initial_prompt[:2000] + "\nLütfen bu maçı analiz et.")

    if "başarısız oldu" in gemini_analysis:
        return default_resp

    await asyncio.sleep(5) # API'ye nefes aldır

    # 2. ADIM: OpenAI "Şeytanın Avukatı"
    openai_prompt = f"""
Sen deneyimli bir bahis uzmanısın. Yapılan şu analizi eleştir ve karşı görüşlerini sun:
Maç: {home_name} vs {away_name}
Analiz: {gemini_analysis}

Atlanan riskleri veya daha mantıklı bahis seçeneklerini belirt. Objektif ve katı ol.
"""
    openai_critique = await analyze_with_openai(openai_prompt)

    await asyncio.sleep(5) # API'ye nefes aldır

    # 3. ADIM: Gemini Final Kararı (Veya Fallback)
    final_prompt = f"""
Sen 'BetBot Baron' AI'sısın. İlk analiz ve OpenAI'ın eleştirileri doğrultusunda son kararını ver. TÜM ANALİZİ TÜRKÇE YAP.
Maç Verisi: {json.dumps(match_data)}
İlk Analiz: {gemini_analysis}
OpenAI Eleştirisi: {openai_critique}

- Oranı 1.35 altı olan "garanti" bahisleri önerme.
- En çok güvendiğin KESİN bahis hedefini 'bet_target' alanına yaz. (HOME_WIN, AWAY_WIN, DRAW, OVER 2.5, UNDER 2.5 vb.)
- SADECE RAW JSON FORMATINDA DÖN VE 'analysis' ALANINI TÜRKÇE DOLDUR:
{{"risk_score": int (0-100), "win_probability": int (0-100), "bet_target": "string", "odds_value": float, "recommendation": "string", "analysis": "string"}}
"""

    final_result_text = ""
    for model_name in AI_MODELS:
        resp = await retry_with_backoff(client.aio.models.generate_content, model=model_name, contents=final_prompt)
        if resp:
            final_result_text = resp.text
            break
    
    if not final_result_text:
        final_result_text = await analyze_with_fallback(final_prompt)

    try:
        match_json = re.search(r'\{.*\}', final_result_text, re.DOTALL)
        if match_json:
            analysis = json.loads(match_json.group())
            analysis["analysis"] = f"AI Sentez: {gemini_analysis[:150]}... | Eleştiri: {openai_critique[:150]}... | Sonuç: {analysis.get('analysis', '')}"
            
            real_odds = extract_real_odds(match_data, analysis.get("bet_target", ""))
            if real_odds > 1.0: analysis["odds_value"] = real_odds
            
            return analysis
    except Exception as e:
        logging.error(f"Final parse error: {e}")

    return default_resp


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
    
    # Eğer oranlar 1.50 - 2.50 dışında ise (çok garanti veya çok riskli) AI'ya sormayalım
    if best_odds > 0 and (best_odds < 1.45 or best_odds > 2.60):
        logging.info(f"Skipping {home_team} vs {away_team} due to odds ({best_odds}) outside target range.")
        return {
            "risk_score": 0, 
            "win_probability": 0, 
            "analysis": f"Oranlar ({best_odds}) hedef aralık dışında olduğu için pas geçildi.", 
            "bet_target": "N/A", 
            "is_recommended": False
        }

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
    risk_info["is_recommended"] = win_prob >= 55 and risk_score < 60
    
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
        
        if risk_info.get("is_recommended") and risk_info.get("bet_target") != "N/A":
            place_virtual_bet(event, risk_info, custom_amount=stake_amount)
        
    return risk_info

async def analyze_odds(odds_data):
    if not odds_data: return []
    # Çoklu analiz sayısını 2'ye düşürerek kotayı koru
    tasks = [analyze_event(event) for event in odds_data[:2]] 
    return await asyncio.gather(*tasks)
