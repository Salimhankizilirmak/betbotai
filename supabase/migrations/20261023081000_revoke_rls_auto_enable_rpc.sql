-- Supabase'in proje oluştururken eklediği "yeni tablolarda RLS'i otomatik aç" event trigger fonksiyonu
-- SECURITY DEFINER ve /rest/v1/rpc üzerinden anon/authenticated tarafından çağrılabilir görünüyordu
-- (advisors 0028/0029). Tetikleyici postgres rolüyle çalışmaya devam eder; sadece API'den çağrılması kapatılır.
do $$
begin
    if exists (
        select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public' and p.proname = 'rls_auto_enable'
    ) then
        revoke execute on function public.rls_auto_enable() from public, anon, authenticated;
    end if;
end $$;
