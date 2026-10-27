-- 27.10.2026: NBA prop bahisleri ve 3 oyunculu prop kuponu kapatıldı (canlıda prop modeli %33 isabet, beklenen %56).
-- Gece kupon işi tetiklenmez; 15:20 UTC props turu projeksiyonları panelde göstermek için sürer.
do $$
begin
    if exists (select 1 from cron.job where jobname = 'betbot-nba-coupon') then
        perform cron.unschedule('betbot-nba-coupon');
    end if;
end $$;
