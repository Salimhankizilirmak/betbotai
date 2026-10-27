"""
Zamanlanmış işler (GitHub Actions veya Windows Görev Zamanlayıcı buradan çalıştırır).

    python jobs.py analyze   # maç oranları: Pinnacle'a göre değer varsa sanal bahis (futbol + basketbol)
    python jobs.py props     # NBA oyuncu prop analizi ve sanal bahis
    python jobs.py resolve   # bekleyen bahisleri sonuçlandır + hatalı sonuçları yeniden kontrol et
    python jobs.py closing   # başlamak üzere olan maçların kapanış oranlarını kaydet
    python jobs.py health    # DB bağlantısı + Odds API kredisi (kota harcamaz) + NBA kaynak erişimi
    python jobs.py nba-ingest    # ESPN'den son NBA maçlarının box score'larını biriktir
    python jobs.py nba-backfill  # önceki sezonun son haftalarını biriktir (sezon başı için, tek sefer)
    python jobs.py football-ingest    # football-data.co.uk bu sezon (TR'den engelli; GitHub Actions'ta çalışır)
    python jobs.py football-backfill  # 2019-20'den bugüne tüm sezonlar (tek sefer)
    python jobs.py football-coupon    # günün futbol kuponu (<= 5 maç, toplam oran <= 8.00) -> Telegram
    python jobs.py nba-coupon         # gece öncesi 3 oyunculu NBA prop kuponu -> Telegram
    python jobs.py learn              # haftalık öğrenme raporu + CLV'ye göre korumalı otomatik ayar -> Telegram

Her komut bir tur çalışır ve çıkar. Ortak durum Supabase'de tutulur (bkz. cache_store.py).
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time

# Arka plan döngüleri bu süreçte başlamasın (main import edilince startup çalışmaz ama yine de açıkça kapat)
os.environ.setdefault("RUN_BACKGROUND_LOOPS", "false")

import main  # noqa: E402  (logging, app ve tur fonksiyonları)
import strategy  # noqa: E402

async def job_analyze():
    return await main.run_analysis_cycle(resolve_first=False)

async def job_props():
    return await main.run_props_cycle(resolve_first=False)

async def job_resolve():
    from ingest.odds_snapshots import prune_snapshots
    import football_data
    await main.run_resolve_cycle(revalidate=True)
    pruned = await asyncio.to_thread(prune_snapshots)
    football = await asyncio.to_thread(football_data.ingest)
    import coupons
    coupon_res = await coupons.resolve_pending()
    return {"ok": True, "snapshots_pruned": pruned, "football_rows": football.get("rows"), "coupons": coupon_res}

async def job_closing():
    from ingest.odds_snapshots import collect_closing_lines
    if not strategy.COLLECT_CLOSING_LINES:
        return {"skipped": "COLLECT_CLOSING_LINES=false"}
    return {"rows_saved": await collect_closing_lines()}

def _nba_stats_check():
    """stats.nba.com bu makineden (ör. GitHub Actions IP'si) erişilebilir mi? Tek istek, kota yok."""
    from nba_api.stats.endpoints import playergamelogs
    from seasons import nba_season
    t0 = time.time()
    try:
        df = playergamelogs.PlayerGameLogs(season_nullable=nba_season(), season_type_nullable="Regular Season",
                                           timeout=30).get_data_frames()[0]
        return {"ok": True, "season": nba_season(), "rows": len(df), "secs": round(time.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:150]}", "secs": round(time.time() - t0, 1)}

def _espn_check():
    """ESPN NBA API (stats.nba.com'a alternatif) bu makineden erişilebilir mi?"""
    import httpx
    t0 = time.time()
    try:
        r = httpx.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard", timeout=15)
        d = r.json() if r.status_code == 200 else {}
        return {"ok": r.status_code == 200, "status": r.status_code, "events": len(d.get("events", [])),
                "secs": round(time.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:150]}", "secs": round(time.time() - t0, 1)}

async def job_health():
    import bet_manager
    from oddsapi_client import get_sports, CREDIT_STATE
    with bet_manager.get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT (SELECT count(*) FROM bets) AS bets, (SELECT count(*) FROM odds_snapshots) AS snapshots, "
                    "(SELECT count(*) FROM app_cache) AS cache_rows")
        counts = dict(cur.fetchone())
    await get_sports()  # /sports kota harcamaz, ama kalan kredi başlığını döner
    nba, espn = await asyncio.gather(asyncio.to_thread(_nba_stats_check), asyncio.to_thread(_espn_check))
    return {"db": counts, "odds_api_credits_remaining": CREDIT_STATE.get("remaining"), "nba_stats_api": nba,
            "espn_api": espn}

async def job_nba_ingest():
    import espn_nba
    return await asyncio.to_thread(espn_nba.ingest_recent)

async def job_nba_backfill():
    """
    Sezon başında oyuncuların yeterli maçı olmadığı için önceki sezonun son haftalarını biriktirir.
    Varsayılan: önceki normal sezonun son ~4 haftası (NBA_BACKFILL_START / NBA_BACKFILL_END ile değiştirilebilir).
    """
    from datetime import date
    import espn_nba
    from seasons import season_start_year, BASKETBALL_SEASON_START_MONTH
    y = season_start_year(None, BASKETBALL_SEASON_START_MONTH)  # 2026-27 sezonu -> 2026
    start = date.fromisoformat(os.getenv("NBA_BACKFILL_START", f"{y}-03-15"))
    end = date.fromisoformat(os.getenv("NBA_BACKFILL_END", f"{y}-04-15"))
    return await asyncio.to_thread(espn_nba.ingest_range, start, end)

async def job_football_ingest():
    import football_data
    return await asyncio.to_thread(football_data.ingest)

async def job_football_backfill():
    import football_data
    return await asyncio.to_thread(football_data.ingest, football_data.all_season_codes())

async def job_football_coupon():
    """Günün futbol kuponu: değer seçimlerinden tek şirket, 2-5 maç, toplam oran <= 8.00 (coupons.py)."""
    import coupons
    from leagues import ANALYZED_SPORTS
    from oddsapi_client import get_odds
    events = []
    for sport in ANALYZED_SPORTS:
        if sport.startswith("soccer"):
            data = await get_odds(sport, max_age=1800)  # kupon için en fazla 30 dk'lık oran
            if isinstance(data, list):
                events.extend(data)
    coupon, stats = coupons.build_football(events)
    return await asyncio.to_thread(coupons.publish, "football", coupon, stats)

async def job_nba_coupon():
    """Gece maçlarından önce: prop turunu tazele, model eşiklerini geçenlerden 3 farklı maçtan 3 oyuncu."""
    import coupons
    from ai_analyzer import NBA_PROPS_CACHE
    if not strategy.NBA_PROP_COUPON_ENABLED:
        return {"skipped": "NBA_PROP_COUPON_ENABLED=false (27.10.2026: prop modeli canlıda beklentinin altında)"}
    props = await main.run_props_cycle(resolve_first=False)
    coupon, stats = coupons.build_nba(NBA_PROPS_CACHE.get("data", []))
    out = await asyncio.to_thread(coupons.publish, "nba_props", coupon, stats)
    return {"props_cycle": props, **out}

async def job_learn():
    """Haftalık öğrenme: CLV/kalibrasyon/segment raporu -> Telegram, CLV'ye göre korumalı otomatik ayar."""
    import learning
    return await asyncio.to_thread(learning.run)

JOBS = {"analyze": job_analyze, "props": job_props, "resolve": job_resolve, "closing": job_closing, "health": job_health,
        "nba-ingest": job_nba_ingest, "nba-backfill": job_nba_backfill,
        "football-ingest": job_football_ingest, "football-backfill": job_football_backfill,
        "football-coupon": job_football_coupon, "nba-coupon": job_nba_coupon, "learn": job_learn}

def run(name):
    t0 = time.time()
    logging.info(f"▶ job '{name}' başladı")
    try:
        result = asyncio.run(JOBS[name]())
    except Exception:
        logging.exception(f"job '{name}' hata ile bitti")
        return 1
    logging.info(f"■ job '{name}' bitti ({time.time() - t0:.0f} sn): {json.dumps(result, default=str, ensure_ascii=False)}")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BetBot zamanlanmış işleri")
    parser.add_argument("job", choices=sorted(JOBS))
    sys.exit(run(parser.parse_args().job))
