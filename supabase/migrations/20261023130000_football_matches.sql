-- Geçmiş futbol maçları ve oranları (football-data.co.uk sezon CSV'leri; bkz. football_data.py).
-- Site Türkiye'den erişilemediği için GitHub Actions'tan doldurulur; model/backtest buradan okur.
create table if not exists public.football_matches (
    match_key text primary key,           -- league|YYYY-MM-DD|home|away
    league text not null,                 -- EPL, LA_LIGA, ...
    season text not null,                 -- '2526'
    match_date date not null,
    kickoff text,
    home_team text not null,
    away_team text not null,
    fthg integer,
    ftag integer,
    hthg integer,
    htag integer,
    home_shots integer,
    away_shots integer,
    home_sot integer,
    away_sot integer,
    odds jsonb,                           -- CSV'deki tüm oran kolonları (B365H, PSCH, PC>2.5, ...)
    updated_at timestamptz not null default now()
);
create index if not exists idx_football_matches_league_date on public.football_matches (league, match_date);
alter table public.football_matches enable row level security;
