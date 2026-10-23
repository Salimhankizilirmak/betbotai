-- BetBot başlangıç şeması.
-- bet_manager.init_db() / db_schema.py aynı tabloları IF NOT EXISTS ile oluşturur; ikisi uyumlu tutulmalı.

create table if not exists public.bets (
    id serial primary key,
    match_id text unique,
    sport_key text,
    home_team text,
    away_team text,
    commence_time text,
    risk_score integer,
    bet_target text,
    odds_value real,
    bet_amount real default 100.0,
    status text default 'PENDING',
    profit real default 0.0,
    created_at timestamp default current_timestamp
);

create table if not exists public.odds_snapshots (
    id bigserial primary key,
    captured_at timestamptz not null default now(),
    snapshot_kind text not null default 'regular',
    sport_key text not null,
    event_id text not null,
    commence_time timestamptz,
    home_team text,
    away_team text,
    bookmaker text not null,
    market text not null,
    selection text not null,
    player text,
    point real,
    price real not null,
    bookmaker_last_update timestamptz
);
create index if not exists idx_odds_snapshots_event on public.odds_snapshots (event_id, market);
create index if not exists idx_odds_snapshots_commence on public.odds_snapshots (commence_time);
create index if not exists idx_odds_snapshots_kind on public.odds_snapshots (snapshot_kind, event_id);

create table if not exists public.predictions (
    id bigserial primary key,
    created_at timestamptz not null default now(),
    model_name text not null,
    model_version text not null,
    sport_key text not null,
    event_id text not null,
    commence_time timestamptz,
    market text not null,
    selection text not null,
    player text,
    point real,
    p_model real not null,
    p_market_fair real,
    price real,
    bookmaker text,
    edge real,
    meta jsonb
);
create index if not exists idx_predictions_event on public.predictions (event_id, market);
create index if not exists idx_predictions_model on public.predictions (model_name, model_version, created_at);

-- Supabase Data API (anon/authenticated anahtarlar) bu tablolara erişemesin:
-- RLS açık ve hiç policy yok. Bot doğrudan postgres rolüyle bağlanır, RLS'ten etkilenmez.
alter table public.bets enable row level security;
alter table public.odds_snapshots enable row level security;
alter table public.predictions enable row level security;
