import os
import time
import asyncio
import httpx
import logging
from dotenv import load_dotenv
from api_key_manager import odds_api_manager

load_dotenv()

BASE_URL = "https://api.the-odds-api.com/v4"

# In-memory cache
CACHE = {}
CACHE_TTL = 1800 # 30 minutes in seconds

# Son yanıttaki kota başlıkları (x-requests-remaining / x-requests-used / x-requests-last)
CREDIT_STATE = {"remaining": None, "used": None, "last_cost": None, "updated_at": None}

def _update_credit_state(response):
    def _num(h):
        v = response.headers.get(h)
        try:
            return float(v) if v is not None else None
        except ValueError:
            return None
    remaining = _num("x-requests-remaining")
    if remaining is not None:
        CREDIT_STATE.update({
            "remaining": remaining,
            "used": _num("x-requests-used"),
            "last_cost": _num("x-requests-last"),
            "updated_at": time.time(),
        })

async def _get_with_rotation(path, params, label):
    """Anahtar rotasyonlu GET; 200 değilse None döner, kota başlıklarını kaydeder."""
    async with httpx.AsyncClient() as client:
        max_retries = odds_api_manager.get_max_retries()
        for attempt in range(max_retries):
            api_key = odds_api_manager.get_current_key()
            if not api_key:
                logging.warning(f"{label}: Odds API anahtarı tanımlı değil.")
                return None
            response = await client.get(f"{BASE_URL}{path}", params={"apiKey": api_key, **params}, timeout=15.0)

            if response.status_code in [401, 403, 429]:
                logging.warning(f"{label}: Odds API key failed ({response.status_code}). Rotating...")
                odds_api_manager.rotate_key()
                await asyncio.sleep(1) # Small delay before retry
                continue

            _update_credit_state(response)
            response.raise_for_status()
            return response.json()
        return None

async def get_sports():
    cache_key = "sports"
    if cache_key in CACHE and time.time() - CACHE[cache_key]["timestamp"] < CACHE_TTL:
        return CACHE[cache_key]["data"]

    data = await _get_with_rotation("/sports", {}, "get_sports")
    if data is not None:
        CACHE[cache_key] = {"data": data, "timestamp": time.time()}
    return data

# Kullanılan marketler; kredi maliyeti = market sayısı x bölge sayısı (spreads kullanılmadığı için çıkarıldı)
DEFAULT_MARKETS = "h2h,totals"
SHARED_CACHE_NAMESPACE = "odds_listing"

async def warm_cache_from_shared(max_age=CACHE_TTL):
    """Ortak önbellekteki tüm oran listelerini tek sorguda belleğe alır (panel ilk açılışı için)."""
    import cache_store
    shared = await asyncio.to_thread(cache_store.get_namespace, SHARED_CACHE_NAMESPACE, max_age)
    now = time.time()
    for key, data in shared.items():
        if key not in CACHE:
            # Yaş bilinmiyor ama max_age içinde; bu süreç için taze say
            CACHE[key] = {"data": data, "timestamp": now}
    return len(shared)

async def get_odds(sport_key="upcoming", region="eu", markets=DEFAULT_MARKETS, force_refresh=False, record=True,
                   max_age=CACHE_TTL):
    """
    force_refresh: önbelleği atlar (kapanış oranı için).
    record: taze çekilen oranları odds_snapshots tablosuna (sadece değişenleri) kaydeder.
    max_age: önbellek ne kadar eski olabilir (sn). Önce bellek, sonra Supabase'deki ortak önbellek
             denenir; böylece Vercel ve GitHub Actions aynı listeyi kredi harcamadan paylaşır.
    """
    import cache_store
    cache_key = f"odds_{sport_key}_{region}_{markets}"
    if not force_refresh:
        if cache_key in CACHE and time.time() - CACHE[cache_key]["timestamp"] < max_age:
            return CACHE[cache_key]["data"]
        shared, ts = await asyncio.to_thread(cache_store.get, SHARED_CACHE_NAMESPACE, cache_key, max_age)
        if shared is not None:
            CACHE[cache_key] = {"data": shared, "timestamp": ts}
            return shared

    params = {"regions": region, "markets": markets, "oddsFormat": "decimal"}
    data = await _get_with_rotation(f"/sports/{sport_key}/odds", params, "get_odds")
    if data is not None:
        CACHE[cache_key] = {"data": data, "timestamp": time.time()}
        await asyncio.to_thread(cache_store.put, SHARED_CACHE_NAMESPACE, cache_key, data)
        if record and sport_key != "upcoming":
            from ingest.odds_snapshots import record_odds_response
            await record_odds_response(data, kind="regular")
    return data

async def get_event_odds(sport_key, event_id, markets, region="us", record=True):
    """Tek maçın oranları (oyuncu prop'ları bu endpoint'ten gelir). Önbelleksiz."""
    params = {"regions": region, "markets": markets, "oddsFormat": "decimal"}
    data = await _get_with_rotation(f"/sports/{sport_key}/events/{event_id}/odds", params, "get_event_odds")
    if data is not None and record:
        from ingest.odds_snapshots import record_odds_response
        await record_odds_response(data, kind="regular")
    return data

async def get_scores(sport_key="upcoming", days_from=3):
    cache_key = f"scores_{sport_key}_{days_from}"
    if cache_key in CACHE and time.time() - CACHE[cache_key]["timestamp"] < CACHE_TTL:
        return CACHE[cache_key]["data"]

    data = await _get_with_rotation(f"/sports/{sport_key}/scores", {"daysFrom": days_from}, "get_scores")
    if data is not None:
        CACHE[cache_key] = {"data": data, "timestamp": time.time()}
    return data
