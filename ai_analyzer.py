import json
from google import genai
import os
from dotenv import load_dotenv
import asyncio
import logging
import time
import re
from data_loader import get_team_stats
from nba_data import get_nba_team_stats
from bet_manager import place_virtual_bet, get_recent_performance

load_dotenv()

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
                elif market["key"] == "totals" and "OVER" in bet_target:
                    for out in market["outcomes"]:
                        if out["name"] == "Over":
                            return float(out["price"])
                elif market["key"] == "totals" and "UNDER" in bet_target:
                    for out in market["outcomes"]:
                        if out["name"] == "Under":
                            return float(out["price"])
    except Exception:
        pass
    return 0.0

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

# Genişletilmiş model listesi (Kota sorunlarını aşmak için)
AI_MODELS = [
    'gemini-2.5-flash',
    'gemini-2.5-flash-lite'
]

CACHE_FILE = os.path.join("data", "ai_cache.json")
CACHE_TTL = 86400  # 24 hours

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

async def calculate_risk(match_data):
    """
    Gemini API'yi kullanarak maç analizi yapar.
    """
    default_resp = {
        "risk_score": 99, 
        "win_probability": 0, 
        "bet_target": "N/A",
        "odds_value": 0.0,
        "recommendation": "Analiz Yapılamadı", 
        "analysis": "Yapay zeka modellerinin tamamı şu an kota limitinde veya hata verdi. Lütfen 5-10 dakika sonra tekrar deneyin."
    }

    if not GEMINI_API_KEY:
        return {"risk_score": 50, "win_probability": 50, "recommendation": "Mock Analizi", "analysis": "Lütfen GEMINI_API_KEY girin."}

    sport_key = match_data.get('sport_key', '')
    home_name = match_data.get('home_team', '')
    away_name = match_data.get('away_team', '')

    home_stats = ""
    away_stats = ""
    
    if "soccer" in sport_key.lower():
        home_stats = await get_team_stats(home_name)
        away_stats = await get_team_stats(away_name)
    
    if "basketball_nba" in sport_key:
        home_stats = await asyncio.to_thread(get_nba_team_stats, home_name)
        away_stats = await asyncio.to_thread(get_nba_team_stats, away_name)

    past_performance = get_recent_performance(limit=10)

    prompt = f"""
Sen uzman bir spor bahisleri yapay zekasısın ("BetBot Baron"). Şu yaklaşan maçı analiz et:
{json.dumps(match_data, indent=2)}

Tarihsel İstatistikler ve Lig Formu (Eğer varsa):
Ev Sahibi: {home_stats}
Deplasman: {away_stats}

Senin Önceki Tahmin Performansın (Öğrenmen İçin):
{past_performance}

- Yanıtını TAMAMEN VE SADECE TÜRKÇE olarak ver.
- En çok güvendiğin KESİN bahis hedefini 'bet_target' alanına yaz (örn: "HOME_WIN", "AWAY_WIN", "DRAW", "OVER_2.5", "UNDER_2.5").
- 'odds_value' alanına seçtiğin hedefin json içindeki güncel bahis oranını ondalık sayı (float) olarak yaz.
SADECE RAW JSON FORMATINDA DÖN:
{{"risk_score": int (0-100), "win_probability": int (0-100), "bet_target": "string", "odds_value": float, "recommendation": "string", "analysis": "string"}}
"""

    for model_name in AI_MODELS:
        attempts = 0
        max_attempts = 1 # Daha fazla model olduğu için attempt sayısını düşürelim ki hızlı dönsün
        
        while attempts < max_attempts:
            try:
                logging.info(f"Gemini AI ({model_name}) analyzing: {home_name} vs {away_name}")
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                
                # Robust JSON Extraction
                match = re.search(r'\{.*\}', response.text, re.DOTALL)
                if not match:
                    raise ValueError("JSON not found in AI response")
                
                analysis = json.loads(match.group())
                
                # Defensive Defaults
                required_keys = ["risk_score", "win_probability", "bet_target", "odds_value", "recommendation", "analysis"]
                for key in required_keys:
                    if key not in analysis:
                        analysis[key] = default_resp[key]
                
                # Ensure types
                analysis["risk_score"] = int(analysis.get("risk_score", 99))
                analysis["win_probability"] = int(analysis.get("win_probability", 0))
                
                # Gerçek oranları API JSON tablosundan matematiksel olarak bularak AI'nin uydurmasını engelle
                real_odds = extract_real_odds(match_data, analysis.get("bet_target", ""))
                if real_odds > 1.0:
                    analysis["odds_value"] = real_odds
                else:
                    analysis["odds_value"] = float(analysis.get("odds_value", 0.0))
                
                return analysis
            except Exception as e:
                attempts += 1
                err_msg = str(e).lower()
                logging.error(f"AI Model {model_name} error: {e}")
                
                if "429" in err_msg or "quota" in err_msg:
                    # Kota sorunu varsa bir süre bekleyip sonraki modele geç
                    await asyncio.sleep(3)
                    break 
                else:
                    break # Diğer hatalarda da sonraki modele geç
                    
    return default_resp

async def analyze_event(event):
    event_id = event.get("id")
    current_time = time.time()
    
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
    tasks = [analyze_event(event) for event in odds_data[:3]] # Paralel sayısını düşür
    return await asyncio.gather(*tasks)
