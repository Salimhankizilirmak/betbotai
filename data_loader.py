import httpx
import pandas as pd
import os
import re
import time
import logging
import asyncio
import unicodedata
from difflib import SequenceMatcher
from seasons import football_season_code

DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# football-data.co.uk sezonluk dosyaları: mmz4281/<sezon kodu>/<dosya>
# Not: Site Türkiye'de BTK tarafından engelli. TR ağlarında HTTPS "self-signed" hatası verir,
# HTTP ise engel sayfası (HTML) döner; bu yüzden SSL doğrulaması açık ve yanıt CSV mi diye kontrol edilir.
FOOTBALL_DATA_BASE = "https://www.football-data.co.uk"
LEAGUE_FILES = {
    "EPL": "E0.csv",
    "LA_LIGA": "SP1.csv",
    "BUNDESLIGA": "D1.csv",
    "SERIE_A": "I1.csv",
    "LIGUE_1": "F1.csv",
    "TURKEY": "T1.csv",
    "TURKEY_2": "T2.csv",
}
STATIC_URLS = {
    "WORLD_CUP": FOOTBALL_DATA_BASE + "/new/world_cup.csv",
    "INT_QUAL": FOOTBALL_DATA_BASE + "/new/euro_qualifiers.csv"
}
# Avrupa kupası takımlarının aranacağı yerel ligler
DOMESTIC_LEAGUES = ["EPL", "LA_LIGA", "BUNDESLIGA", "SERIE_A", "LIGUE_1", "TURKEY"]

CSV_REFRESH_SECONDS = 12 * 3600  # Sezon içinde CSV'ler haftada birkaç kez güncellenir

def get_league_url(league, season_code=None):
    if league in STATIC_URLS:
        return STATIC_URLS[league]
    fname = LEAGUE_FILES.get(league)
    if not fname:
        return None
    return f"{FOOTBALL_DATA_BASE}/mmz4281/{season_code or football_season_code()}/{fname}"

def _looks_like_results_csv(content: bytes) -> bool:
    head = content[:500].lstrip(b"\xef\xbb\xbf").lstrip()
    return head.startswith(b"Div,") or b"HomeTeam" in head.split(b"\n", 1)[0]

# Per-league cache: league -> {"df": DataFrame, "ts": float, "season": str}
_df_cache = {}
_lock = asyncio.Lock()

async def get_dataframe(league="EPL"):
    """
    Belirli bir lig için güncel sezon verilerini indirir ve DataFrame olarak döner.
    Dosya 12 saatten eskiyse yeniden indirilir; indirme başarısız olursa eldeki dosya kullanılır.
    """
    season = football_season_code()
    CSV_URL = get_league_url(league, season)
    if not CSV_URL:
        logging.warning(f"No URL defined for league: {league}")
        return None

    CSV_PATH = os.path.join(DATA_DIR, f"{league}_{season}_historical.csv")

    async with _lock:
        cached = _df_cache.get(league)
        if cached and cached["season"] == season and time.time() - cached["ts"] < CSV_REFRESH_SECONDS:
            return cached["df"]

        is_stale = not os.path.exists(CSV_PATH) or time.time() - os.path.getmtime(CSV_PATH) > CSV_REFRESH_SECONDS
        if is_stale:
            logging.info(f"Downloading historical data: {league} ({season})...")
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        response = await client.get(CSV_URL)
                    if response.status_code == 200 and not _looks_like_results_csv(response.content):
                        logging.error(f"Download {league}: yanıt CSV değil (erişim engeli/hata sayfası olabilir), kaydedilmedi.")
                        break
                    if response.status_code == 200:
                        with open(CSV_PATH, 'wb') as f:
                            f.write(response.content)
                        logging.info(f"Successfully downloaded: {league} ({season})")
                        break
                    logging.warning(f"Download {league} failed with status {response.status_code}")
                except Exception as e:
                    logging.error(f"Attempt {attempt+1} failed for {league}: {e}")
                await asyncio.sleep(2)

        if not os.path.exists(CSV_PATH):
            return None

        try:
            df = pd.read_csv(CSV_PATH)
            _df_cache[league] = {"df": df, "ts": time.time(), "season": season}
            return df
        except Exception as e:
            logging.error(f"Error reading CSV {CSV_PATH}: {e}")
            return None

# Odds API isimleri -> football-data.co.uk isimleri (normalize edilmiş haliyle)
TEAM_ALIASES = {
    "manchester united": "man united",
    "manchester city": "man city",
    "wolverhampton wanderers": "wolves",
    "nottingham forest": "nott'm forest",
    "newcastle united": "newcastle",
    "tottenham hotspur": "tottenham",
    "west ham united": "west ham",
    "brighton and hove albion": "brighton",
    "leeds united": "leeds",
    "sheffield united": "sheffield united",
    "atletico madrid": "ath madrid",
    "athletic bilbao": "ath bilbao",
    "real sociedad": "sociedad",
    "real betis": "betis",
    "rayo vallecano": "vallecano",
    "celta vigo": "celta",
    "espanyol": "espanol",
    "deportivo alaves": "alaves",
    "borussia dortmund": "dortmund",
    "borussia monchengladbach": "m'gladbach",
    "bayer leverkusen": "leverkusen",
    "eintracht frankfurt": "ein frankfurt",
    "fc koln": "fc koln",
    "1. fc koln": "fc koln",
    "vfb stuttgart": "stuttgart",
    "fsv mainz 05": "mainz",
    "tsg hoffenheim": "hoffenheim",
    "inter milan": "inter",
    "ac milan": "milan",
    "as roma": "roma",
    "paris saint germain": "paris sg",
    "paris saint-germain": "paris sg",
    "olympique marseille": "marseille",
    "olympique lyonnais": "lyon",
    "as monaco": "monaco",
    "istanbul basaksehir": "buyuksehyr",
    "basaksehir": "buyuksehyr",
    "fatih karagumruk": "karagumruk",
}

# Tek başına takım ayırt etmeyen kelimeler ("Real" Madrid/Sociedad/Betis'in hepsinde var)
GENERIC_TOKENS = {"fc", "cf", "ac", "as", "sc", "sk", "afc", "club", "real", "united", "city",
                  "de", "fk", "jk", "spor", "calcio", "cd", "ud", "sd", "rc", "vfb", "vfl", "tsg", "1."}

def normalize_team_name(name):
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"\s+", " ", name.lower().replace("&", "and")).strip()
    return name

def resolve_team_name(team_name, candidates, min_score=0.6):
    """Bir takım adını CSV'deki takım listesinden en iyi eşleşenle eşler. Bulamazsa None."""
    target = normalize_team_name(team_name)
    target = TEAM_ALIASES.get(target, target)
    target_tokens = {t for t in target.split() if t not in GENERIC_TOKENS}

    best, best_score = None, 0.0
    for cand in candidates:
        c = normalize_team_name(cand)
        if c == target:
            return cand
        c_tokens = {t for t in c.split() if t not in GENERIC_TOKENS}
        token_score = len(target_tokens & c_tokens) / len(target_tokens | c_tokens) if (target_tokens or c_tokens) else 0
        score = max(token_score, SequenceMatcher(None, target, c).ratio())
        if score > best_score:
            best, best_score = cand, score
    return best if best_score >= min_score else None

def _team_summary(df, team, label):
    home_matches = df[df['HomeTeam'] == team]
    away_matches = df[df['AwayTeam'] == team]
    total_matches = len(home_matches) + len(away_matches)
    if total_matches == 0:
        return None

    def _avg(home_col, away_col):
        if home_col in df.columns and away_col in df.columns:
            return round((home_matches[home_col].sum() + away_matches[away_col].sum()) / total_matches, 2)
        return 0

    avg_goals = _avg('FTHG', 'FTAG')
    avg_conceded = _avg('FTAG', 'FTHG')
    avg_corners = _avg('HC', 'AC')
    avg_yellows = _avg('HY', 'AY')
    return f"{team} ({label} - Son {total_matches} Maç) - Atılan: {avg_goals}, Yenen: {avg_conceded}, Korner: {avg_corners}, Kart: {avg_yellows}"

async def get_team_stats(team_name, league_key="EPL"):
    """
    Belirli bir lig içindeki takımın bu sezonki performans özetini döner.
    """
    if not team_name:
        return ""

    df = await get_dataframe(league_key)
    if df is None:
        return f"{league_key} için tarihsel veri bulunamadı."

    try:
        teams = pd.concat([df['HomeTeam'], df['AwayTeam']]).dropna().unique()
        team = resolve_team_name(team_name, teams)
        if not team:
            return f"{team_name} için {league_key} verisi bulunamadı."
        return _team_summary(df, team, league_key) or f"{team_name} için {league_key} verisi bulunamadı."
    except Exception as e:
        return f"{league_key} veri okuma hatası: {e}"

async def get_team_stats_any_league(team_name):
    """Avrupa kupaları için: takımı tüm yerel lig CSV'lerinde arar ve kendi liginin istatistiğini döner."""
    if not team_name:
        return ""
    for league_key in DOMESTIC_LEAGUES:
        df = await get_dataframe(league_key)
        if df is None:
            continue
        teams = pd.concat([df['HomeTeam'], df['AwayTeam']]).dropna().unique()
        team = resolve_team_name(team_name, teams, min_score=0.75)
        if team:
            summary = _team_summary(df, team, f"{league_key} yerel lig")
            if summary:
                return summary
    return f"{team_name} için yerel lig verisi bulunamadı (takip edilmeyen lig)."
