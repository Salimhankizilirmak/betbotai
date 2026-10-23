"""
football-data.co.uk sezon CSV'lerini Supabase football_matches tablosuna yükler.

Site Türkiye'de BTK engelli (HTTPS self-signed engel sayfası döner); bu yüzden GitHub Actions'tan çalıştırılır:
    python jobs.py football-ingest            # bu sezon (her sonuçlandırma turunda)
    python jobs.py football-backfill          # 2019-20'den bugüne tüm sezonlar (tek sefer)
"""
import io
import logging
from datetime import date

import httpx
import pandas as pd

from data_loader import FOOTBALL_DATA_BASE, LEAGUE_FILES, _looks_like_results_csv
from seasons import season_start_year, FOOTBALL_SEASON_START_MONTH

MODEL_LEAGUES = ["EPL", "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1", "TURKEY"]
FIRST_SEASON_START = 2019

STAT_COLS = {"FTHG": "fthg", "FTAG": "ftag", "HTHG": "hthg", "HTAG": "htag",
             "HS": "home_shots", "AS": "away_shots", "HST": "home_sot", "AST": "away_sot"}
NON_ODDS = {"Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTR", "HTR", "Referee", "HF", "AF", "HC", "AC",
            "HY", "AY", "HR", "AR", "Attendance", "HHW", "AHW", "HO", "AO", "HBP", "ABP", *STAT_COLS}

def season_code(start_year):
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"

def all_season_codes(today=None):
    last = season_start_year(today, FOOTBALL_SEASON_START_MONTH)
    return [season_code(y) for y in range(FIRST_SEASON_START, last + 1)]

def download(league, code):
    url = f"{FOOTBALL_DATA_BASE}/mmz4281/{code}/{LEAGUE_FILES[league]}"
    r = httpx.get(url, timeout=30, follow_redirects=True)
    if r.status_code != 200 or not _looks_like_results_csv(r.content):
        logging.warning(f"football-data {league} {code}: {r.status_code} / CSV değil")
        return None
    text = r.content.decode("utf-8-sig", errors="replace")
    return pd.read_csv(io.StringIO(text), on_bad_lines="skip")

def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None

def _float(v):
    try:
        f = float(v)
        return f if f == f else None  # NaN -> None
    except (TypeError, ValueError):
        return None

def parse(df, league, code):
    rows = []
    if df is None or df.empty:
        return rows
    odds_cols = [c for c in df.columns if c not in NON_ODDS and not str(c).startswith("Unnamed")]
    for _, r in df.iterrows():
        if pd.isna(r.get("HomeTeam")) or pd.isna(r.get("AwayTeam")) or pd.isna(r.get("Date")):
            continue
        d = pd.to_datetime(str(r["Date"]), dayfirst=True, errors="coerce")
        if pd.isna(d):
            continue
        odds = {c: _float(r[c]) for c in odds_cols if _float(r[c]) is not None}
        row = {"match_key": f"{league}|{d.date()}|{r['HomeTeam']}|{r['AwayTeam']}",
               "league": league, "season": code, "match_date": d.date(),
               "kickoff": None if pd.isna(r.get("Time")) else str(r.get("Time")),
               "home_team": str(r["HomeTeam"]).strip(), "away_team": str(r["AwayTeam"]).strip(), "odds": odds}
        for src, dst in STAT_COLS.items():
            row[dst] = _int(r.get(src))
        rows.append(row)
    return rows

COLS = ["match_key", "league", "season", "match_date", "kickoff", "home_team", "away_team", "fthg", "ftag", "hthg",
        "htag", "home_shots", "away_shots", "home_sot", "away_sot", "odds"]

def upsert(rows):
    if not rows:
        return 0
    rows = list({r["match_key"]: r for r in rows}.values())  # aynı toplu INSERT'te tekrar eden anahtar olmasın
    from psycopg2.extras import execute_values, Json
    from bet_manager import get_db_connection
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS if c != "match_key")
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, f"""
                INSERT INTO football_matches ({", ".join(COLS)}) VALUES %s
                ON CONFLICT (match_key) DO UPDATE SET {updates}, updated_at = NOW()
            """, [tuple(Json(r[c]) if c == "odds" else r[c] for c in COLS) for r in rows], page_size=500)
        conn.commit()
    return len(rows)

def ingest(season_codes=None, leagues=None):
    season_codes = season_codes or all_season_codes()[-1:]
    leagues = leagues or MODEL_LEAGUES
    total, report = 0, {}
    for code in season_codes:
        for lg in leagues:
            n = upsert(parse(download(lg, code), lg, code))
            report[f"{lg}-{code}"] = n
            total += n
    logging.info(f"⚽ football-data ingest: {total} maç ({len(report)} lig-sezon)")
    return {"rows": total, "by_league_season": report}

def load_matches(leagues=None):
    """Model/backtest için tüm maçlar (skoru olanlar)."""
    from bet_manager import get_db_connection
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM football_matches WHERE fthg IS NOT NULL AND league = ANY(%s) ORDER BY match_date",
                    (leagues or MODEL_LEAGUES,))
        return pd.DataFrame([dict(r) for r in cur.fetchall()])
