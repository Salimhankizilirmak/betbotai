"""
NBA takım/oyuncu verileri — ESPN kaynaklı (espn_nba.py).

stats.nba.com GitHub Actions ve Supabase gibi bulut IP'lerini engellediği için nba_api uç noktaları
kullanılmaz. Fonksiyon imzaları eski nba_api sürümüyle aynıdır; çağıran kod değişmez.
"""
import logging
import threading
import time
from datetime import timedelta
from difflib import SequenceMatcher

import pandas as pd

import espn_nba

STANDINGS_TTL = 3600
_standings = {"df": None, "ts": 0.0}
_lock = threading.Lock()

def fetch_nba_standings():
    """
    Puan durumu DataFrame'i (eski LeagueStandingsV3 kolon adlarıyla):
    TeamName (Celtics), TeamCity, DisplayName (Boston Celtics), TeamAbbr, TeamID, WinPCT, Record, CurrentStreak, L10
    """
    with _lock:
        if _standings["df"] is not None and time.time() - _standings["ts"] < STANDINGS_TTL:
            return _standings["df"]
        try:
            rows = espn_nba.fetch_standings()
            if not rows:
                return _standings["df"]
            df = pd.DataFrame([{
                "TeamName": r["name"],
                "DisplayName": r["display_name"],
                "TeamAbbr": r["abbr"],
                "TeamID": r["team_id"],
                "WinPCT": r["win_pct"],
                "Record": r["record"],
                "CurrentStreak": r["streak"],
                "L10": r["l10"],
            } for r in rows])
            _standings.update(df=df, ts=time.time())
            return df
        except Exception as e:
            logging.error(f"Error fetching NBA standings (ESPN): {e}")
            return _standings["df"]

def _standings_row(df, team_name):
    if df is None or not team_name:
        return None
    for _, row in df.iterrows():
        if team_match(team_name, row["DisplayName"]):
            return row
    last_word = team_name.split()[-1]
    match = df[df["TeamName"].str.contains(last_word, case=False, na=False)]
    return match.iloc[0] if not match.empty else None

def fetch_nba_h2h(team_a_name, team_b_name):
    """İki takım (tam ad) arasındaki bu sezonki maçlar."""
    try:
        games = espn_nba.season_games_between(team_a_name, team_b_name)
        if not games:
            return "Bu sezon henüz karşılaşmadılar."
        return " | ".join(
            f"{g['game_date']}: {g['away_abbr']} @ {g['home_abbr']} {g['away_score']}-{g['home_score']}" for g in games)
    except Exception as e:
        logging.error(f"Error fetching H2H: {e}")
        return "H2H verisi alınamadı."

def get_nba_top_players(team_abbr):
    """Takımın bu sezonki en skorer 3 oyuncusu."""
    try:
        top = espn_nba.team_top_scorers(team_abbr)
        if not top:
            return ""
        return "Kilit Oyuncular: " + ", ".join(
            f"{r['player_name']} ({float(r['pts']):.1f} PTS, {float(r['reb']):.1f} REB, {float(r['ast']):.1f} AST, {r['gp']} maç)"
            for r in top)
    except Exception as e:
        logging.error(f"Error fetching NBA player stats: {e}")
        return ""

def get_nba_team_stats(team_name, opponent_name=None):
    if not team_name:
        return ""
    df = fetch_nba_standings()
    if df is None:
        return "NBA API stat unavailable."
    try:
        row = _standings_row(df, team_name)
        if row is None:
            return f"No direct NBA stats found for {team_name}."
        player_info = get_nba_top_players(row["TeamAbbr"])
        h2h_info = ""
        if opponent_name:
            opp = _standings_row(df, opponent_name)
            if opp is not None:
                h2h_info = f"\nBu Sezon H2H: {fetch_nba_h2h(row['DisplayName'], opp['DisplayName'])}"
        return (f"{team_name} - Record: {row['Record']} (Win %: {row['WinPCT']:.3f}), Streak: {row['CurrentStreak']}, "
                f"L10: {row['L10']}. {player_info} {h2h_info}")
    except Exception as e:
        return f"Error reading NBA stats: {e}"

def _date_window(date_str):
    """commence_time (UTC) -> ABD maç günü ve bir önceki gün (geç saat farkı için)."""
    d = espn_nba.us_game_date(str(date_str).split(".")[0])
    return [d, d - timedelta(days=1)]

def get_nba_player_game_stat(player_name, date_str, stat_type):
    """
    Belirli bir tarihteki maçta oyuncunun istatistiği (prop sonuçlandırma).
    date_str: '2026-03-27T03:00:00Z' veya '2026-03-27 03:00:00' (UTC)
    stat_type: 'PTS', 'REB', 'AST'
    Döner: değer, oynamadıysa -999.0 (DNP -> iade), veri yoksa None.
    """
    try:
        window = _date_window(date_str)
        espn_nba.ensure_dates(window)
        rows = espn_nba.player_rows_on_dates(player_name, window)
        played = [r for r in rows if not r["dnp"]]
        if played:
            val = played[0].get(stat_type.lower())
            logging.info(f"✅ Found NBA Stat (ESPN): {player_name} {stat_type} = {val} ({played[0]['game_date']})")
            return float(val or 0)
        if rows:
            logging.warning(f"DNP: {player_name} ({rows[0].get('dnp_reason')}) {rows[0]['game_date']}")
            return -999.0
        # Box score'da hiç yoksa: takımı o gün oynayıp maç bittiyse kadroda değil (DNP), yoksa henüz veri yok
        team = espn_nba.player_last_team(player_name)
        if team and any(g["completed"] for g in espn_nba.team_games_on_dates(team, window)):
            logging.warning(f"DNP/Missing: {player_name} box score'da yok, takımı ({team}) oynadı")
            return -999.0
        if not team:
            logging.warning(f"Player NOT FOUND in ESPN data: '{player_name}'")
        return None
    except Exception as e:
        logging.error(f"Error resolving NBA player stat: {e}")
        return None

def get_nba_match_score(home_team, away_team, date_str):
    """Maç skoru (Odds API yedeği)."""
    try:
        window = _date_window(date_str)
        espn_nba.ensure_dates(window)
        for g in espn_nba.games_on_dates(window):
            if not g["completed"]:
                continue
            if team_match(home_team, g["home_team"]) and team_match(away_team, g["away_team"]):
                h, a = g["home_score"], g["away_score"]
            elif team_match(home_team, g["away_team"]) and team_match(away_team, g["home_team"]):
                h, a = g["away_score"], g["home_score"]
            else:
                continue
            logging.info(f"✅ NBA FALLBACK SUCCESS (ESPN): {home_team} {h} - {a} {away_team} ({g['game_date']})")
            return {"home_score": h, "away_score": a, "completed": True}
        return None
    except Exception as e:
        logging.error(f"Error in NBA match score fallback: {e}")
        return None

def team_match(a, b):
    """
    NBA takım adı eşleşmesi: tam ad veya lakap (son kelime) aynıysa. Lakaplar ligde benzersizdir;
    dfm'den farklı olarak 'Los Angeles Lakers' ile 'Los Angeles Clippers'ı eşleştirmez.
    """
    if not a or not b:
        return False
    na, nb = espn_nba.player_key(a), espn_nba.player_key(b)
    return bool(na) and bool(nb) and (na == nb or na.split()[-1] == nb.split()[-1])

def dfm(n1, n2, threshold=0.7):
    """Helper for internal fuzzy matching without importing bet_manager."""
    if not n1 or not n2: return False
    n1, n2 = str(n1).lower().strip(), str(n2).lower().strip()
    if n1 in n2 or n2 in n1: return True
    w1 = set(n1.split()); w2 = set(n2.split())
    if w1.intersection(w2) and any(len(w) > 3 for w in w1.intersection(w2)): return True
    return SequenceMatcher(None, n1, n2).ratio() >= threshold
