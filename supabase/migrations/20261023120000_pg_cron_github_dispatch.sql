-- Zamanlayıcı: Supabase pg_cron, GitHub Actions workflow'larını workflow_dispatch API'si ile tetikler.
-- GitHub'ın kendi 'schedule' tetikleyicileri bu repoda çalışmadığı için zamanlama tek yerden buradan yapılır.
--
-- Gerekli: Supabase Vault'ta 'github_dispatch_token' adlı secret (sadece bu repo, Actions: Read and write
-- yetkili fine-grained GitHub token). Yoksa fonksiyon hiçbir şey yapmaz.
--   select vault.create_secret('<token>', 'github_dispatch_token', 'GitHub Actions workflow_dispatch');

create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;

-- API'ye açık olmayan şema (Supabase Data API sadece public'i açar)
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create or replace function private.dispatch_github_workflow(workflow text, ref text default 'main')
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    token text;
    request_id bigint;
begin
    select decrypted_secret into token
    from vault.decrypted_secrets where name = 'github_dispatch_token' limit 1;
    if token is null then
        raise notice 'github_dispatch_token yok; % tetiklenmedi', workflow;
        return null;
    end if;
    select net.http_post(
        url := 'https://api.github.com/repos/Salimhankizilirmak/betbotai/actions/workflows/' || workflow || '/dispatches',
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || token,
            'Accept', 'application/vnd.github+json',
            'X-GitHub-Api-Version', '2022-11-28',
            'User-Agent', 'betbot-pg-cron',
            'Content-Type', 'application/json'),
        body := jsonb_build_object('ref', ref)
    ) into request_id;
    return request_id;
end;
$$;
revoke all on function private.dispatch_github_workflow(text, text) from public, anon, authenticated;

-- Saatler UTC (eski workflow 'schedule' satırlarıyla aynı). cron.schedule aynı adla tekrar çağrılınca günceller.
select cron.schedule('betbot-closing',  '*/10 * * * *',  $$select private.dispatch_github_workflow('job-closing.yml')$$);
select cron.schedule('betbot-analyze',  '5 */3 * * *',   $$select private.dispatch_github_workflow('job-analyze.yml')$$);
select cron.schedule('betbot-props',    '20 15,22 * * *', $$select private.dispatch_github_workflow('job-props.yml')$$);
select cron.schedule('betbot-resolve',  '35 */4 * * *',  $$select private.dispatch_github_workflow('job-resolve.yml')$$);
