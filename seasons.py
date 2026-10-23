"""
Sezon kodlarını tarihten otomatik hesaplar (koda gömülü "2025-26" gibi değerler yerine).

- Avrupa futbolu Ağustos'ta başlar: Ağustos 2026 - Temmuz 2027 => 2026 sezonu.
- NBA ve Euroleague Ekim'de başlar: Ekim 2026 - Eylül 2027 => 2026 sezonu.
  Sezon arasında (Temmuz-Eylül) bir önceki, tamamlanmış sezon döner.
"""
from datetime import date, datetime

FOOTBALL_SEASON_START_MONTH = 8
BASKETBALL_SEASON_START_MONTH = 10


def _to_date(d=None):
    if d is None:
        return date.today()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    # "2026-10-23T02:00:00Z" veya "2026-10-23 02:00:00"
    return datetime.fromisoformat(str(d).replace("Z", "+00:00").replace(" ", "T")).date()


def season_start_year(d=None, start_month=FOOTBALL_SEASON_START_MONTH):
    d = _to_date(d)
    return d.year if d.month >= start_month else d.year - 1


def football_season_code(d=None):
    """football-data.co.uk klasör kodu, örn. 2026-27 sezonu için '2627'."""
    y = season_start_year(d, FOOTBALL_SEASON_START_MONTH)
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def football_season_year(d=None):
    """Premier League Pulselive API sezon yılı, örn. '2026'."""
    return str(season_start_year(d, FOOTBALL_SEASON_START_MONTH))


def nba_season(d=None):
    """nba_api sezon formatı, örn. '2026-27'."""
    y = season_start_year(d, BASKETBALL_SEASON_START_MONTH)
    return f"{y}-{(y + 1) % 100:02d}"


def nba_regular_season_id(d=None):
    """LeagueGameFinder SEASON_ID değeri, örn. '22026' (2 = normal sezon)."""
    return f"2{season_start_year(d, BASKETBALL_SEASON_START_MONTH)}"


def euroleague_season_code(d=None):
    """Euroleague feed sezon kodu, örn. 'E2026'."""
    return f"E{season_start_year(d, BASKETBALL_SEASON_START_MONTH)}"
