-- Öğrenme raporu (learning.py) için bahis meta verisi: hangi şirket/market/yöntem, bahis anındaki Pinnacle olasılığı.
alter table public.bets add column if not exists bookmaker text;
alter table public.bets add column if not exists market text;
alter table public.bets add column if not exists p_fair real;
alter table public.bets add column if not exists method text;

-- Mevcut bahisleri doldur: değer bahisleri predictions tablosundaki ilk kayıttan, prop'lar match_id'den
update public.bets b set bookmaker = p.bookmaker, market = p.market, p_fair = p.p_market_fair, method = 'value_vs_pinnacle'
from (
    select distinct on (event_id, selection) event_id, selection, bookmaker, market, p_market_fair
    from public.predictions where model_name in ('value_vs_pinnacle', 'football_value')
    order by event_id, selection, created_at
) p
where b.method is null and b.match_id = p.event_id and b.bet_target = p.selection;

update public.bets set method = 'nba_props', market = 'player_prop', p_fair = (100 - risk_score) / 100.0
where method is null and match_id like 'PROP\_%' escape '\';

update public.bets set method = 'llm_legacy' where method is null;

-- Öğrenme ayarları/raporu app_cache'te tutulur; haftalık iş Pazartesi 08:00 UTC (TR 11:00)
select cron.schedule('betbot-learn', '0 8 * * 1', $$select private.dispatch_github_workflow('job-learn.yml')$$);
