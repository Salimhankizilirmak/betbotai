-- Vercel fonksiyonları ve GitHub Actions işleri arasında paylaşılan önbellek
-- (AI analizleri, NBA prop sonuçları, oran listeleri, revalidation durumu). Bkz. cache_store.py
create table if not exists public.app_cache (
    namespace text not null,
    key text not null,
    value jsonb not null,
    updated_at timestamptz not null default now(),
    primary key (namespace, key)
);
create index if not exists idx_app_cache_updated on public.app_cache (namespace, updated_at);

alter table public.app_cache enable row level security;
