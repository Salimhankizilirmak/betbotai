"""Botun takip ettiği ligler (The Odds API sport key'leri). Tek yerden yönetilir."""

# Avrupa kupaları: takımlar farklı yerel liglerden geldiği için istatistikler tüm lig CSV'lerinde aranır
UEFA_COMPETITIONS = [
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
]

DOMESTIC_FOOTBALL = [
    "soccer_turkey_super_league",
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
]

BASKETBALL = [
    "basketball_nba",
    "basketball_euroleague",
]

ANALYZED_SPORTS = DOMESTIC_FOOTBALL + UEFA_COMPETITIONS + BASKETBALL

# Odds API sport key -> football-data.co.uk lig anahtarı (data_loader.LEAGUE_FILES)
ODDS_TO_STATS_MAP = {
    "soccer_epl": "EPL",
    "soccer_spain_la_liga": "LA_LIGA",
    "soccer_italy_serie_a": "SERIE_A",
    "soccer_germany_bundesliga": "BUNDESLIGA",
    "soccer_france_ligue_one": "LIGUE_1",
    "soccer_turkey_super_league": "TURKEY",
    "soccer_turkey_1_league": "TURKEY_2",
}
