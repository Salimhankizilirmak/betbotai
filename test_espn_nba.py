"""ESPN NBA veri katmanı için çevrimdışı testler: python test_espn_nba.py"""
from datetime import date

import espn_nba
import nba_data
from nba_player_props import _logs_to_form, _find_opponent

SCOREBOARD = {"events": [
    {"id": "401", "date": "2026-10-22T23:00Z", "season": {"slug": "regular-season"},
     "competitions": [{"status": {"type": {"name": "STATUS_FINAL", "completed": True}}, "competitors": [
         {"homeAway": "home", "score": "114", "team": {"displayName": "Philadelphia 76ers", "abbreviation": "PHI"}},
         {"homeAway": "away", "score": "106", "team": {"displayName": "Cleveland Cavaliers", "abbreviation": "CLE"}}]}]},
    {"id": "999", "date": "2026-10-03T23:00Z", "season": {"slug": "preseason"}, "competitions": [{}]},
]}

SUMMARY = {"boxscore": {"players": [{
    "team": {"abbreviation": "CLE", "displayName": "Cleveland Cavaliers"},
    "statistics": [{
        "keys": ["minutes", "points", "fieldGoalsMade-fieldGoalsAttempted",
                 "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "freeThrowsMade-freeThrowsAttempted",
                 "rebounds", "assists", "turnovers", "steals", "blocks"],
        "athletes": [
            {"starter": True, "didNotPlay": False, "athlete": {"id": "4432158", "displayName": "Evan Mobley"},
             "stats": ["34", "18", "7-13", "1-4", "3-3", "8", "2", "1", "0", "0"]},
            {"starter": False, "didNotPlay": True, "reason": "COACH'S DECISION",
             "athlete": {"id": "4431734", "displayName": "Tristan Enaruna"}, "stats": []},
        ]}]}]}}


def test_player_key():
    assert espn_nba.player_key("C.J. McCollum") == "cj mccollum"
    assert espn_nba.player_key("Nikola Jokić") == "nikola jokic"
    assert espn_nba.player_key("Jaren Jackson Jr.") == "jaren jackson"
    assert espn_nba.player_key("De'Aaron Fox") == "deaaron fox"
    assert espn_nba.player_key("Kelly Oubre Jr") == espn_nba.player_key("Kelly Oubre Jr.")


def test_us_game_date():
    # 02:30 UTC maçı ABD'de bir önceki günün akşamı
    assert espn_nba.us_game_date("2026-10-24T02:30:00Z") == date(2026, 10, 23)
    assert espn_nba.us_game_date("2026-10-23 23:00:00") == date(2026, 10, 23)


def test_parse_scoreboard_skips_preseason():
    games = espn_nba.parse_scoreboard(SCOREBOARD, date(2026, 10, 22))
    assert len(games) == 1
    g = games[0]
    assert g["game_id"] == "401" and g["completed"] and g["season"] == "2026-27"
    assert (g["home_abbr"], g["away_abbr"], g["home_score"], g["away_score"]) == ("PHI", "CLE", 114, 106)


def test_parse_boxscore():
    g = espn_nba.parse_scoreboard(SCOREBOARD, date(2026, 10, 22))[0]
    rows = espn_nba.parse_boxscore(SUMMARY, g)
    assert len(rows) == 2
    mobley, dnp = rows
    assert (mobley["minutes"], mobley["pts"], mobley["reb"], mobley["ast"], mobley["fg3m"]) == (34, 18, 8, 2, 1)
    assert mobley["opponent_abbr"] == "PHI" and mobley["is_home"] is False and mobley["player_key"] == "evan mobley"
    assert dnp["dnp"] and dnp["pts"] is None and dnp["dnp_reason"] == "COACH'S DECISION"


def test_parse_boxscore_skips_athletes_without_id():
    import copy
    summary = copy.deepcopy(SUMMARY)
    summary["boxscore"]["players"][0]["statistics"][0]["athletes"].append(
        {"starter": False, "didNotPlay": True, "reason": "COACH'S DECISION", "athlete": {}, "stats": []})
    g = espn_nba.parse_scoreboard(SCOREBOARD, date(2026, 10, 22))[0]
    assert len(espn_nba.parse_boxscore(summary, g)) == 2


def test_team_match_no_same_city_confusion():
    assert nba_data.team_match("Los Angeles Clippers", "LA Clippers")
    assert nba_data.team_match("Portland Trail Blazers", "Portland Trail Blazers")
    assert not nba_data.team_match("Los Angeles Lakers", "Los Angeles Clippers")
    assert not nba_data.team_match("New York Knicks", "Brooklyn Nets")


def test_previous_season():
    assert espn_nba.previous_season("2026-27") == "2025-26"
    assert espn_nba.previous_season("2099-00") == "2098-99"


def test_logs_to_form_and_opponent():
    rows = [
        {"game_date": date(2026, 10, 22), "team_abbr": "CLE", "team_name": "Cleveland Cavaliers", "is_home": False,
         "opponent_abbr": "PHI", "opponent_name": "Philadelphia 76ers", "season": "2026-27", "pts": 18, "reb": 8, "ast": 2},
        {"game_date": date(2026, 4, 12), "team_abbr": "CLE", "team_name": "Cleveland Cavaliers", "is_home": True,
         "opponent_abbr": "NY", "opponent_name": "New York Knicks", "season": "2025-26", "pts": 22, "reb": 10, "ast": 3},
    ]
    form = _logs_to_form(rows, "PTS")
    assert form["last_games"] == [18, 22] and form["avg"] == 20.0
    assert form["last_games_detail"][0]["matchup"] == "CLE @ PHI"
    assert form["last_games_detail"][1]["matchup"] == "CLE vs. NY"
    detail = form["last_games_detail"]
    assert _find_opponent(detail, "Cleveland Cavaliers", "Boston Celtics") == "Boston Celtics"
    assert _find_opponent(detail, "Boston Celtics", "Cleveland Cavaliers") == "Boston Celtics"
    assert _find_opponent(detail, "Miami Heat", "Boston Celtics") == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
