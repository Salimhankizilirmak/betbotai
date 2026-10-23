"""Faz 1 düzeltmeleri için çevrimdışı testler: python -m pytest test_phase1.py (veya python test_phase1.py)"""
import asyncio
from datetime import date

import seasons
from data_loader import resolve_team_name
from leagues import ANALYZED_SPORTS


def test_seasons_2026_27():
    d = date(2026, 10, 23)
    assert seasons.nba_season(d) == "2026-27"
    assert seasons.nba_regular_season_id(d) == "22026"
    assert seasons.euroleague_season_code(d) == "E2026"
    assert seasons.football_season_code(d) == "2627"
    assert seasons.football_season_year(d) == "2026"


def test_season_boundaries():
    # Futbol Ağustos'ta, basketbol Ekim'de yeni sezona geçer
    assert seasons.football_season_code(date(2026, 7, 31)) == "2526"
    assert seasons.football_season_code(date(2026, 8, 1)) == "2627"
    assert seasons.nba_season(date(2026, 9, 30)) == "2025-26"
    assert seasons.nba_season(date(2027, 4, 15)) == "2026-27"
    # Prop çözümleme commence_time string'i ile çağırır
    assert seasons.nba_season("2026-10-24T02:00:00Z") == "2026-27"
    assert seasons.nba_season("2026-10-24 02:00:00") == "2026-27"


def test_team_resolution_no_first_word_collision():
    la_liga = ["Real Madrid", "Sociedad", "Betis", "Barcelona", "Ath Madrid", "Ath Bilbao", "Vallecano"]
    assert resolve_team_name("Real Madrid", la_liga) == "Real Madrid"
    assert resolve_team_name("Real Sociedad", la_liga) == "Sociedad"
    assert resolve_team_name("Real Betis", la_liga) == "Betis"
    assert resolve_team_name("Atlético Madrid", la_liga) == "Ath Madrid"
    epl = ["Man United", "Man City", "Tottenham", "Nott'm Forest", "Wolves"]
    assert resolve_team_name("Manchester United", epl) == "Man United"
    assert resolve_team_name("Manchester City", epl) == "Man City"
    turkey = ["Galatasaray", "Fenerbahce", "Besiktas", "Buyuksehyr"]
    assert resolve_team_name("Fenerbahçe", turkey) == "Fenerbahce"
    assert resolve_team_name("Beşiktaş JK", turkey) == "Besiktas"
    assert resolve_team_name("Liverpool", turkey) is None


def test_team_stats_from_csv(monkeypatch=None):
    import pandas as pd
    import data_loader
    df = pd.DataFrame({
        "HomeTeam": ["Real Madrid", "Sociedad", "Betis"], "AwayTeam": ["Betis", "Real Madrid", "Sociedad"],
        "FTHG": [3, 0, 2], "FTAG": [1, 2, 2], "HC": [5, 4, 6], "AC": [3, 7, 2], "HY": [1, 2, 3], "AY": [2, 1, 1],
    })

    async def fake_df(league="EPL"):
        return df
    original = data_loader.get_dataframe
    data_loader.get_dataframe = fake_df
    try:
        out = asyncio.run(data_loader.get_team_stats("Real Madrid", "LA_LIGA"))
        # Real Madrid: 2 maç, attığı 3+2, yediği 1+0; "Real" kelimesi Sociedad/Betis'i karıştırmamalı
        assert "Son 2 Maç" in out and "Atılan: 2.5" in out and "Yenen: 0.5" in out, out
    finally:
        data_loader.get_dataframe = original


def test_block_page_rejected():
    from data_loader import _looks_like_results_csv
    assert not _looks_like_results_csv(b'<html><head><style>.erisime_engellenmis{}</style>')
    assert _looks_like_results_csv(b"\xef\xbb\xbfDiv,Date,Time,HomeTeam,AwayTeam,FTHG\nT1,01/08/26")


def test_uefa_leagues_tracked():
    for key in ("soccer_uefa_champs_league", "soccer_uefa_europa_league", "soccer_uefa_europa_conference_league"):
        assert key in ANALYZED_SPORTS


def test_prop_opponent_detection():
    from nba_player_props import _find_opponent
    detail = [{"matchup": "LAL vs. BOS"}]
    assert _find_opponent(detail, "Los Angeles Lakers", "Boston Celtics") == "Boston Celtics"
    detail = [{"matchup": "BOS @ LAL"}]
    assert _find_opponent(detail, "Los Angeles Lakers", "Boston Celtics") == "Los Angeles Lakers"
    assert _find_opponent([{"matchup": "MIA vs. NYK"}], "Los Angeles Lakers", "Boston Celtics") == ""


def test_analyze_event_without_markets_returns_default():
    from ai_analyzer import analyze_event
    res = asyncio.run(analyze_event({"id": "x", "home_team": "A", "away_team": "B", "bookmakers": []}))
    assert res["bet_target"] == "N/A"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
