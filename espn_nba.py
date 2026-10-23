"""
ESPN NBA veri kaynağı. stats.nba.com GitHub Actions / Supabase gibi bulut IP'lerini engellediği için
NBA maçları ve oyuncu box score'ları ESPN'in açık API'sinden çekilip Supabase'e biriktirilir:

- nba_games:        maç programı ve skorlar (scoreboard?dates=YYYYMMDD)
- nba_player_games: maç başına oyuncu istatistikleri (summary?event=ID), DNP dahil

ingest_recent() tekrar tekrar çalıştırılabilir (upsert); props ve sonuçlandırma işleri önce bunu çağırır.
"""
import logging
import re
import time
import unicodedata
from datetime import date, datetime, timedelta

import httpx
import pytz

from seasons import nba_season

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
ET = pytz.timezone("America/New_York")
SEASON_TYPES = {"regular-season", "post-season", "play-in"}
FIRST_RUN_BACKFILL_DAYS = 21

_client = None

def _http():
    global _client
    if _client is None:
        # ESPN tanınmayan/bot User-Agent'larına 403 döner; standart tarayıcı UA'sı kullanılır
        _client = httpx.Client(timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/140.0.0.0 Safari/537.36",
            "Accept": "application/json",
        })
    return _client

def _get_json(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = _http().get(url, params=params)
            if r.status_code == 200:
                return r.json()
            logging.warning(f"ESPN {url} -> {r.status_code}")
        except Exception as e:
            logging.warning(f"ESPN istek hatası ({attempt + 1}/{retries}): {e}")
        time.sleep(1 + attempt)
    return None

# ---------------- isim normalizasyonu ----------------
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

def player_key(name):
    """'C.J. McCollum' -> 'cj mccollum', 'Nikola Jokić' -> 'nikola jokic', 'Jaren Jackson Jr.' -> 'jaren jackson'."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[.'`’]", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t not in _SUFFIXES]
    return " ".join(tokens)

def us_game_date(utc_dt):
    """Odds API commence_time (UTC) -> ABD Eastern maç günü."""
    if isinstance(utc_dt, str):
        utc_dt = datetime.fromisoformat(utc_dt.replace("Z", "+00:00").replace(" ", "T"))
    if utc_dt.tzinfo is None:
        utc_dt = pytz.utc.localize(utc_dt)
    return utc_dt.astimezone(ET).date()

def today_et():
    return datetime.now(ET).date()

# ---------------- ESPN parse ----------------
def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None

def parse_scoreboard(data, game_date):
    games = []
    for e in (data or {}).get("events", []):
        if (e.get("season") or {}).get("slug") not in SEASON_TYPES:
            continue
        comp = (e.get("competitions") or [{}])[0]
        st = (comp.get("status") or {}).get("type") or {}
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        home, away = teams.get("home", {}), teams.get("away", {})
        games.append({
            "game_id": str(e["id"]),
            "game_date": game_date,
            "season": nba_season(game_date),
            "start_time": e.get("date"),
            "status": st.get("name"),
            "completed": bool(st.get("completed")),
            "home_team": (home.get("team") or {}).get("displayName"),
            "away_team": (away.get("team") or {}).get("displayName"),
            "home_abbr": (home.get("team") or {}).get("abbreviation"),
            "away_abbr": (away.get("team") or {}).get("abbreviation"),
            "home_score": _int(home.get("score")),
            "away_score": _int(away.get("score")),
        })
    return games

def parse_boxscore(summary, game):
    """summary JSON -> oyuncu satırları. DNP oyuncular da (dnp=True) döner."""
    rows = []
    teams = (summary or {}).get("boxscore", {}).get("players", [])
    for t in teams:
        team = t.get("team") or {}
        abbr = team.get("abbreviation")
        is_home = abbr == game["home_abbr"]
        opp_abbr = game["away_abbr"] if is_home else game["home_abbr"]
        opp_name = game["away_team"] if is_home else game["home_team"]
        for stat_block in t.get("statistics", [])[:1]:
            keys = stat_block.get("keys") or []
            idx = {k: i for i, k in enumerate(keys)}
            for a in stat_block.get("athletes", []):
                ath = a.get("athlete") or {}
                stats = a.get("stats") or []
                dnp = bool(a.get("didNotPlay")) or not stats

                def stat(k):
                    i = idx.get(k)
                    return _int(stats[i]) if (i is not None and i < len(stats)) else None

                fg3 = stats[idx["threePointFieldGoalsMade-threePointFieldGoalsAttempted"]] \
                    if (not dnp and "threePointFieldGoalsMade-threePointFieldGoalsAttempted" in idx) else None
                rows.append({
                    "game_id": game["game_id"],
                    "athlete_id": str(ath.get("id")),
                    "game_date": game["game_date"],
                    "season": game["season"],
                    "player_name": ath.get("displayName"),
                    "player_key": player_key(ath.get("displayName", "")),
                    "team_abbr": abbr,
                    "team_name": team.get("displayName"),
                    "opponent_abbr": opp_abbr,
                    "opponent_name": opp_name,
                    "is_home": is_home,
                    "starter": bool(a.get("starter")),
                    "dnp": dnp,
                    "dnp_reason": a.get("reason") if dnp else None,
                    "minutes": None if dnp else stat("minutes"),
                    "pts": None if dnp else stat("points"),
                    "reb": None if dnp else stat("rebounds"),
                    "ast": None if dnp else stat("assists"),
                    "fg3m": _int(str(fg3).split("-")[0]) if fg3 else None,
                    "stl": None if dnp else stat("steals"),
                    "blk": None if dnp else stat("blocks"),
                    "tov": None if dnp else stat("turnovers"),
                })
    return rows

# ---------------- DB ----------------
GAME_COLS = ["game_id", "game_date", "season", "start_time", "status", "completed", "home_team", "away_team",
             "home_abbr", "away_abbr", "home_score", "away_score"]
PLAYER_COLS = ["game_id", "athlete_id", "game_date", "season", "player_name", "player_key", "team_abbr", "team_name",
               "opponent_abbr", "opponent_name", "is_home", "starter", "dnp", "dnp_reason", "minutes", "pts", "reb",
               "ast", "fg3m", "stl", "blk", "tov"]

def _db():
    from bet_manager import get_db_connection
    return get_db_connection()

def upsert_games(cur, games):
    from psycopg2.extras import execute_values
    if not games:
        return
    execute_values(cur, f"""
        INSERT INTO nba_games ({", ".join(GAME_COLS)}) VALUES %s
        ON CONFLICT (game_id) DO UPDATE SET
            status = EXCLUDED.status, completed = EXCLUDED.completed, start_time = EXCLUDED.start_time,
            home_score = EXCLUDED.home_score, away_score = EXCLUDED.away_score, updated_at = NOW()
    """, [tuple(g[c] for c in GAME_COLS) for g in games])

def upsert_players(cur, rows):
    from psycopg2.extras import execute_values
    if not rows:
        return
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in PLAYER_COLS if c not in ("game_id", "athlete_id"))
    execute_values(cur, f"""
        INSERT INTO nba_player_games ({", ".join(PLAYER_COLS)}) VALUES %s
        ON CONFLICT (game_id, athlete_id) DO UPDATE SET {updates}
    """, [tuple(r[c] for c in PLAYER_COLS) for r in rows])

def ingest_date(d):
    """Bir ABD maç gününün programını ve biten maçların box score'larını yazar. (maç, oyuncu satırı) döner."""
    data = _get_json(f"{SITE}/scoreboard", {"dates": d.strftime("%Y%m%d")})
    if data is None:
        return 0, 0
    games = parse_scoreboard(data, d)
    n_players = 0
    with _db() as conn:
        with conn.cursor() as cur:
            upsert_games(cur, games)
            cur.execute("SELECT game_id FROM nba_games WHERE game_date = %s AND boxscore_ingested", (d,))
            done = {r["game_id"] for r in cur.fetchall()}
            for g in games:
                if not g["completed"] or g["game_id"] in done:
                    continue
                summary = _get_json(f"{SITE}/summary", {"event": g["game_id"]})
                rows = parse_boxscore(summary, g)
                if not rows:
                    continue
                upsert_players(cur, rows)
                cur.execute("UPDATE nba_games SET boxscore_ingested = TRUE WHERE game_id = %s", (g["game_id"],))
                n_players += len(rows)
        conn.commit()
    return len(games), n_players

def ingest_recent(max_days=None):
    """
    Son biriktirilen günden (bir gün geriden, geç biten maçlar için) bugüne kadar olan günleri işler.
    Tablo boşsa FIRST_RUN_BACKFILL_DAYS gün geriye gider.
    """
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(game_date) AS d FROM nba_games WHERE boxscore_ingested")
            last = cur.fetchone()["d"]
    except Exception as e:
        logging.error(f"nba_games okuma hatası: {e}")
        return {"error": str(e)}
    end = today_et()
    start = (last - timedelta(days=1)) if last else end - timedelta(days=FIRST_RUN_BACKFILL_DAYS)
    if max_days:
        start = max(start, end - timedelta(days=max_days))
    total_games = total_rows = 0
    d = start
    while d <= end:
        g, p = ingest_date(d)
        total_games += g
        total_rows += p
        d += timedelta(days=1)
    logging.info(f"🏀 ESPN ingest {start}..{end}: {total_games} maç, {total_rows} yeni oyuncu satırı")
    return {"from": str(start), "to": str(end), "games": total_games, "player_rows": total_rows}

def ingest_range(start, end):
    """Belirli bir tarih aralığını biriktirir (ör. sezon başı için önceki sezonun son haftaları)."""
    total_games = total_rows = 0
    d = start
    while d <= end:
        g, p = ingest_date(d)
        total_games += g
        total_rows += p
        d += timedelta(days=1)
    logging.info(f"🏀 ESPN backfill {start}..{end}: {total_games} maç, {total_rows} oyuncu satırı")
    return {"from": str(start), "to": str(end), "games": total_games, "player_rows": total_rows}

def ensure_dates(dates):
    """Sonuçlandırma için: bu günlerde bitmiş ama box score'u alınmamış maç varsa çeker."""
    for d in sorted(set(dates)):
        if d > today_et():
            continue
        with _db() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n, count(*) FILTER (WHERE completed AND NOT boxscore_ingested) AS pending "
                        "FROM nba_games WHERE game_date = %s", (d,))
            r = cur.fetchone()
        if r["n"] == 0 or r["pending"] > 0 or d >= today_et() - timedelta(days=1):
            ingest_date(d)

# ---------------- sorgular ----------------
def previous_season(season):
    """'2026-27' -> '2025-26'"""
    y = int(season[:4]) - 1
    return f"{y}-{(y + 1) % 100:02d}"

def player_logs(names, season=None, limit=None, fill_previous_season=False):
    """
    {player_key: [oynadığı maçlar, yeniden eskiye]} — DNP'ler hariç (nba_api game log ile aynı mantık).
    fill_previous_season: sezon başında limit'e ulaşamayan oyuncular önceki sezonun son maçlarıyla tamamlanır.
    """
    keys = list({player_key(n) for n in names})
    if not keys:
        return {}
    season = season or nba_season()
    seasons = [season, previous_season(season)] if fill_previous_season else [season]
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT * FROM nba_player_games
            WHERE player_key = ANY(%s) AND season = ANY(%s) AND NOT dnp AND coalesce(minutes, 0) > 0
            ORDER BY player_key, game_date DESC
        """, (keys, seasons))
        out = {}
        for r in cur.fetchall():
            lst = out.setdefault(r["player_key"], [])
            if limit is None or len(lst) < limit:
                lst.append(dict(r))
    return out

def player_rows_on_dates(name, dates):
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM nba_player_games WHERE player_key = %s AND game_date = ANY(%s) ORDER BY game_date DESC",
                    (player_key(name), list(dates)))
        return [dict(r) for r in cur.fetchall()]

def player_last_team(name):
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT team_abbr FROM nba_player_games WHERE player_key = %s ORDER BY game_date DESC LIMIT 1",
                    (player_key(name),))
        r = cur.fetchone()
        return r["team_abbr"] if r else None

def team_games_on_dates(team_abbr, dates):
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM nba_games WHERE game_date = ANY(%s) AND (home_abbr = %s OR away_abbr = %s)",
                    (list(dates), team_abbr, team_abbr))
        return [dict(r) for r in cur.fetchall()]

def games_on_dates(dates):
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM nba_games WHERE game_date = ANY(%s)", (list(dates),))
        return [dict(r) for r in cur.fetchall()]

def season_games_between(team_a, team_b, season=None):
    """İki takım (tam ad) arasındaki bu sezonki bitmiş maçlar."""
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT * FROM nba_games WHERE season = %s AND completed
              AND ((home_team = %s AND away_team = %s) OR (home_team = %s AND away_team = %s))
            ORDER BY game_date
        """, (season or nba_season(), team_a, team_b, team_b, team_a))
        return [dict(r) for r in cur.fetchall()]

def team_top_scorers(team_abbr, n=3, season=None):
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT player_name, count(*) AS gp, avg(pts) AS pts, avg(reb) AS reb, avg(ast) AS ast
            FROM nba_player_games
            WHERE season = %s AND team_abbr = %s AND NOT dnp AND coalesce(minutes, 0) > 0
            GROUP BY player_name ORDER BY avg(pts) DESC NULLS LAST LIMIT %s
        """, (season or nba_season(), team_abbr, n))
        return [dict(r) for r in cur.fetchall()]

def fetch_standings():
    """[{team_id, abbr, name (Celtics), display_name, win_pct, record, streak, l10}]"""
    data = _get_json(STANDINGS_URL)
    out = []
    for conf in (data or {}).get("children", []):
        for e in (conf.get("standings") or {}).get("entries", []):
            t = e.get("team") or {}
            s = {x.get("name"): x for x in e.get("stats", [])}
            l10 = next((x.get("displayValue") for x in e.get("stats", []) if x.get("type") == "lasttengames"), None)
            out.append({
                "team_id": t.get("id"),
                "abbr": t.get("abbreviation"),
                "name": t.get("shortDisplayName") or t.get("name"),
                "display_name": t.get("displayName"),
                "win_pct": float((s.get("winPercent") or {}).get("value") or 0.0),
                "record": next((x.get("displayValue") for x in e.get("stats", []) if x.get("type") == "total"), None),
                "streak": (s.get("streak") or {}).get("displayValue"),
                "l10": l10,
            })
    return out
