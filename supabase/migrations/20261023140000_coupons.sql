-- Günlük kuponlar (futbol: değer seçimlerinden <= 5 maç, toplam oran <= 8.00; NBA: 3 oyuncu prop).
-- Tekli bahis kasasından ayrı, sabit stake ile izlenir (bkz. coupons.py).
create table if not exists public.coupons (
    id bigserial primary key,
    kind text not null,                   -- 'football' | 'nba_props'
    coupon_date date not null,            -- TR günü
    created_at timestamptz not null default now(),
    bookmaker text,
    legs jsonb not null,                  -- [{event_id, sport_key, home_team, away_team, commence_time, pick, odds, p_fair, ...}]
    total_odds real not null,
    p_fair real,                          -- bağımsızlık varsayımıyla tutma olasılığı
    ev real,
    stake real not null default 100,
    status text not null default 'PENDING',   -- PENDING | WON | LOST | VOID
    profit real not null default 0,
    resolved_at timestamptz,
    telegram_sent boolean not null default false,
    unique (kind, coupon_date)
);
create index if not exists idx_coupons_status on public.coupons (status, kind);
alter table public.coupons enable row level security;

-- Zamanlama (UTC): futbol kuponu 09:00 (TR 12:00); NBA kuponu 21:40 (TR 00:40, ilk maçlardan önce).
-- NBA kupon işi props turunu tazeleyerek çalıştırdığı için 22:20 props turu kaldırılır (15:20 kalır).
select cron.schedule('betbot-football-coupon', '0 9 * * *',   $$select private.dispatch_github_workflow('job-football-coupon.yml')$$);
select cron.schedule('betbot-nba-coupon',      '40 21 * * *', $$select private.dispatch_github_workflow('job-nba-coupon.yml')$$);
select cron.schedule('betbot-props',           '20 15 * * *', $$select private.dispatch_github_workflow('job-props.yml')$$);
