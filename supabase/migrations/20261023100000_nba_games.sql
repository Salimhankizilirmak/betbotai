-- NBA maçları ve oyuncu box score'ları (ESPN'den, bkz. espn_nba.py).
-- stats.nba.com bulut IP'lerini engellediği için oyuncu maç kayıtları burada biriktirilir.
create table if not exists public.nba_games (
    game_id text primary key,
    game_date date not null,              -- ABD (Eastern) maç günü
    season text not null,                 -- '2026-27'
    start_time timestamptz,
    status text,
    completed boolean not null default false,
    home_team text,
    away_team text,
    home_abbr text,
    away_abbr text,
    home_score integer,
    away_score integer,
    boxscore_ingested boolean not null default false,
    updated_at timestamptz not null default now()
);
create index if not exists idx_nba_games_date on public.nba_games (game_date);

create table if not exists public.nba_player_games (
    game_id text not null references public.nba_games (game_id) on delete cascade,
    athlete_id text not null,
    game_date date not null,
    season text not null,
    player_name text not null,
    player_key text not null,             -- normalize edilmiş ad (aksan/nokta/Jr. yok)
    team_abbr text,
    team_name text,
    opponent_abbr text,
    opponent_name text,
    is_home boolean,
    starter boolean,
    dnp boolean not null default false,
    dnp_reason text,
    minutes real,
    pts integer,
    reb integer,
    ast integer,
    fg3m integer,
    stl integer,
    blk integer,
    tov integer,
    primary key (game_id, athlete_id)
);
create index if not exists idx_nba_player_games_player on public.nba_player_games (player_key, game_date desc);
create index if not exists idx_nba_player_games_team on public.nba_player_games (season, team_abbr);

alter table public.nba_games enable row level security;
alter table public.nba_player_games enable row level security;
