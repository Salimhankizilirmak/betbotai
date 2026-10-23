"""
Zamanlanmış işler (GitHub Actions veya Windows Görev Zamanlayıcı buradan çalıştırır).

    python jobs.py analyze   # maç oranlarını çek, yeni maçları analiz et, sanal bahis al
    python jobs.py props     # NBA oyuncu prop analizi ve sanal bahis
    python jobs.py resolve   # bekleyen bahisleri sonuçlandır + hatalı sonuçları yeniden kontrol et
    python jobs.py closing   # başlamak üzere olan maçların kapanış oranlarını kaydet
    python jobs.py health    # DB bağlantısı + Odds API kredisi (kota harcamaz)

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

MAX_NEW_ANALYSES = int(os.getenv("MAX_NEW_ANALYSES", "15"))

async def job_analyze():
    return await main.run_analysis_cycle(resolve_first=False, max_new_analyses=MAX_NEW_ANALYSES)

async def job_props():
    return await main.run_props_cycle(resolve_first=False)

async def job_resolve():
    from ingest.odds_snapshots import prune_snapshots
    await main.run_resolve_cycle(revalidate=True)
    pruned = await asyncio.to_thread(prune_snapshots)
    return {"ok": True, "snapshots_pruned": pruned}

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

JOBS = {"analyze": job_analyze, "props": job_props, "resolve": job_resolve, "closing": job_closing, "health": job_health}

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
