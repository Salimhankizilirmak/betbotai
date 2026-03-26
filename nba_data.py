from nba_api.stats.endpoints import leaguestandingsv3, teamplayerdashboard, leaguegamefinder
from nba_api.stats.static import teams as nba_teams
import logging
import threading
import pandas as pd
import time

_nba_stats = None
_player_cache = {}
_h2h_cache = {}
_lock = threading.Lock()

def fetch_nba_standings():
    global _nba_stats
    if _nba_stats is not None:
        return _nba_stats
        
    with _lock:
        if _nba_stats is not None:
            return _nba_stats
        try:
            logging.info("Fetching NBA standings from nba_api (Timeout 5s)...")
            standings = leaguestandingsv3.LeagueStandingsV3(timeout=5).get_data_frames()[0]
            _nba_stats = standings
            logging.info("NBA standings fetched successfully.")
            return _nba_stats
        except Exception as e:
            logging.error(f"Error fetching NBA stats: {e}")
            return None

def fetch_nba_h2h(team_a_id, team_b_id):
    """İki takım arasındaki bu sezonki maçları getirir."""
    cache_key = tuple(sorted([team_a_id, team_b_id]))
    if cache_key in _h2h_cache:
        return _h2h_cache[cache_key]

    try:
        logging.info(f"Fetching H2H stats for teams {team_a_id} and {team_b_id}")
        gamefinder = leaguegamefinder.LeagueGameFinder(team_id_nullable=team_a_id, vs_team_id_nullable=team_b_id).get_data_frames()[0]
        
        # Sadece bu sezonu filtrele (2024-25)
        current_season_games = gamefinder[gamefinder['SEASON_ID'].str.contains('22024')] # 2 -> Regular Season, 2024 -> Year
        
        results = []
        for _, row in current_season_games.iterrows():
            date = row['GAME_DATE']
            matchup = row['MATCHUP']
            wl = row['WL']
            pts = row['PTS']
            opp_pts = row['PTS'] - row['PLUS_MINUS']
            results.append(f"{date}: {matchup} ({wl}) {pts}-{int(opp_pts)}")
        
        h2h_str = " | ".join(results) if results else "Bu sezon henüz karşılaşmadılar."
        _h2h_cache[cache_key] = h2h_str
        return h2h_str
    except Exception as e:
        logging.error(f"Error fetching H2H: {e}")
        return "H2H verisi alınamadı."

def get_nba_team_stats(team_name, opponent_name=None):
    if not team_name:
        return ""
        
    df = fetch_nba_standings()
    if df is None:
        return "NBA API stat unavailable."
        
    try:
        last_word = team_name.split()[-1]
        team_row = df[df['TeamName'].str.contains(last_word, case=False, na=False)]
        
        if team_row.empty:
            return f"No direct NBA stats found for {team_name}."
            
        record = team_row.iloc[0].get('Record', 'N/A')
        win_pct = team_row.iloc[0].get('WinPCT', '0.00')
        streak = team_row.iloc[0].get('CurrentStreak', 'N/A')
        l10 = team_row.iloc[0].get('L10', 'N/A')
        team_id = team_row.iloc[0].get('TeamID')
        
        # Player Stats (Top 3 Scorers)
        player_info = get_nba_top_players(team_id) if team_id else ""
        
        h2h_info = ""
        if opponent_name:
            opp_last_word = opponent_name.split()[-1]
            opp_row = df[df['TeamName'].str.contains(opp_last_word, case=False, na=False)]
            if not opp_row.empty:
                opp_id = opp_row.iloc[0].get('TeamID')
                h2h_info = f"\nBu Sezon H2H: {fetch_nba_h2h(team_id, opp_id)}"
        
        return f"{team_name} - Record: {record} (Win %: {win_pct}), Streak: {streak}, L10: {l10}. {player_info} {h2h_info}"
        
    except Exception as e:
        return f"Error reading NBA stats: {e}"

def get_nba_top_players(team_id):
    """
    Takımdaki en iyi 3 skorerin istatistiklerini getirir.
    """
    if team_id in _player_cache:
        return _player_cache[team_id]
        
    try:
        logging.info(f"Fetching player stats for Team ID: {team_id}")
        dash = teamplayerdashboard.TeamPlayerDashboard(team_id=team_id, season='2024-25', timeout=10).get_data_frames()[1]
        
        # Sort by PTS
        top_scorers = dash.sort_values(by='PTS', ascending=False).head(3)
        
        stats_str = "Kilit Oyuncular: "
        for _, row in top_scorers.iterrows():
            name = row['PLAYER_NAME']
            pts = row['PTS']
            reb = row['REB']
            ast = row['AST']
            stats_str += f"{name} ({pts} PTS, {reb} REB, {ast} AST), "
            
        _player_cache[team_id] = stats_str.strip(", ")
        return _player_cache[team_id]
    except Exception as e:
        logging.error(f"Error fetching NBA player stats: {e}")
        return ""

