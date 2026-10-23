"""
Faz 2 tabloları. bet_manager.init_db() her açılışta çağırır; tüm ifadeler idempotent (IF NOT EXISTS).

- odds_snapshots: Odds API'den çekilen her oranın zaman damgalı kopyası (açılış/ara/kapanış).
  Backtest'te CLV ve NBA prop edge'i bu tablodan ölçülür.
- predictions: Modellerin her tahmini; piyasanın marjsız olasılığı ve edge ile birlikte saklanır.
"""

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS odds_snapshots (
        id BIGSERIAL PRIMARY KEY,
        captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        snapshot_kind TEXT NOT NULL DEFAULT 'regular',
        sport_key TEXT NOT NULL,
        event_id TEXT NOT NULL,
        commence_time TIMESTAMPTZ,
        home_team TEXT,
        away_team TEXT,
        bookmaker TEXT NOT NULL,
        market TEXT NOT NULL,
        selection TEXT NOT NULL,
        player TEXT,
        point REAL,
        price REAL NOT NULL,
        bookmaker_last_update TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_odds_snapshots_event ON odds_snapshots (event_id, market)",
    "CREATE INDEX IF NOT EXISTS idx_odds_snapshots_commence ON odds_snapshots (commence_time)",
    "CREATE INDEX IF NOT EXISTS idx_odds_snapshots_kind ON odds_snapshots (snapshot_kind, event_id)",
    """
    CREATE TABLE IF NOT EXISTS predictions (
        id BIGSERIAL PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        model_name TEXT NOT NULL,
        model_version TEXT NOT NULL,
        sport_key TEXT NOT NULL,
        event_id TEXT NOT NULL,
        commence_time TIMESTAMPTZ,
        market TEXT NOT NULL,
        selection TEXT NOT NULL,
        player TEXT,
        point REAL,
        p_model REAL NOT NULL,
        p_market_fair REAL,
        price REAL,
        bookmaker TEXT,
        edge REAL,
        meta JSONB
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_predictions_event ON predictions (event_id, market)",
    "CREATE INDEX IF NOT EXISTS idx_predictions_model ON predictions (model_name, model_version, created_at)",
    """
    CREATE TABLE IF NOT EXISTS app_cache (
        namespace TEXT NOT NULL,
        key TEXT NOT NULL,
        value JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (namespace, key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_app_cache_updated ON app_cache (namespace, updated_at)",
    "ALTER TABLE app_cache ENABLE ROW LEVEL SECURITY",
    # NBA maçları ve box score'lar (ESPN); supabase/migrations/*_nba_games.sql ile aynı
    """
    CREATE TABLE IF NOT EXISTS nba_games (
        game_id TEXT PRIMARY KEY, game_date DATE NOT NULL, season TEXT NOT NULL, start_time TIMESTAMPTZ,
        status TEXT, completed BOOLEAN NOT NULL DEFAULT FALSE, home_team TEXT, away_team TEXT,
        home_abbr TEXT, away_abbr TEXT, home_score INTEGER, away_score INTEGER,
        boxscore_ingested BOOLEAN NOT NULL DEFAULT FALSE, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_nba_games_date ON nba_games (game_date)",
    """
    CREATE TABLE IF NOT EXISTS nba_player_games (
        game_id TEXT NOT NULL REFERENCES nba_games (game_id) ON DELETE CASCADE, athlete_id TEXT NOT NULL,
        game_date DATE NOT NULL, season TEXT NOT NULL, player_name TEXT NOT NULL, player_key TEXT NOT NULL,
        team_abbr TEXT, team_name TEXT, opponent_abbr TEXT, opponent_name TEXT, is_home BOOLEAN, starter BOOLEAN,
        dnp BOOLEAN NOT NULL DEFAULT FALSE, dnp_reason TEXT, minutes REAL, pts INTEGER, reb INTEGER, ast INTEGER,
        fg3m INTEGER, stl INTEGER, blk INTEGER, tov INTEGER, PRIMARY KEY (game_id, athlete_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_nba_player_games_player ON nba_player_games (player_key, game_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_nba_player_games_team ON nba_player_games (season, team_abbr)",
    # Geçmiş futbol maçları + oranlar (football-data.co.uk); supabase/migrations/*_football_matches.sql ile aynı
    """
    CREATE TABLE IF NOT EXISTS football_matches (
        match_key TEXT PRIMARY KEY, league TEXT NOT NULL, season TEXT NOT NULL, match_date DATE NOT NULL,
        kickoff TEXT, home_team TEXT NOT NULL, away_team TEXT NOT NULL, fthg INTEGER, ftag INTEGER, hthg INTEGER,
        htag INTEGER, home_shots INTEGER, away_shots INTEGER, home_sot INTEGER, away_sot INTEGER, odds JSONB,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_football_matches_league_date ON football_matches (league, match_date)",
    "ALTER TABLE football_matches ENABLE ROW LEVEL SECURITY",
    # Günlük kuponlar; supabase/migrations/*_coupons.sql ile aynı
    """
    CREATE TABLE IF NOT EXISTS coupons (
        id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL, coupon_date DATE NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), bookmaker TEXT, legs JSONB NOT NULL, total_odds REAL NOT NULL,
        p_fair REAL, ev REAL, stake REAL NOT NULL DEFAULT 100, status TEXT NOT NULL DEFAULT 'PENDING',
        profit REAL NOT NULL DEFAULT 0, resolved_at TIMESTAMPTZ, telegram_sent BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE (kind, coupon_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_coupons_status ON coupons (status, kind)",
    "ALTER TABLE coupons ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE nba_games ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE nba_player_games ENABLE ROW LEVEL SECURITY",
    # Supabase Data API (anon anahtar) erişemesin; bot postgres rolüyle bağlanır, RLS'ten etkilenmez.
    # supabase/migrations/*_initial_schema.sql ile aynı.
    "ALTER TABLE bets ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE odds_snapshots ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE predictions ENABLE ROW LEVEL SECURITY",
]

def ensure_schema(cursor):
    for stmt in SCHEMA_STATEMENTS:
        cursor.execute(stmt)
