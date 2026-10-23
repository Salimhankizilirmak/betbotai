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
    # Supabase Data API (anon anahtar) erişemesin; bot postgres rolüyle bağlanır, RLS'ten etkilenmez.
    # supabase/migrations/*_initial_schema.sql ile aynı.
    "ALTER TABLE bets ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE odds_snapshots ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE predictions ENABLE ROW LEVEL SECURITY",
]

def ensure_schema(cursor):
    for stmt in SCHEMA_STATEMENTS:
        cursor.execute(stmt)
