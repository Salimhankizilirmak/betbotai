"""
Süreçler arası ortak önbellek (Supabase app_cache tablosu).

Vercel fonksiyonları ve GitHub Actions işleri ayrı süreçlerde çalıştığı için bellek içi
sözlükler ve yerel dosyalar paylaşılamaz. AI analizleri, NBA prop sonuçları, oran listeleri ve
revalidation durumu burada tutulur. Tüm fonksiyonlar hata durumunda sessizce boş döner;
önbellek hatası bot akışını durdurmamalı.
"""
import json
import logging
from datetime import datetime, timezone

def _conn():
    from bet_manager import get_db_connection
    return get_db_connection()

def get(namespace, key, max_age_seconds=None):
    """(value, updated_at_epoch) veya (None, None)."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT value, updated_at FROM app_cache WHERE namespace = %s AND key = %s", (namespace, key))
            row = cur.fetchone()
        if not row:
            return None, None
        ts = row["updated_at"].timestamp()
        if max_age_seconds is not None and datetime.now(timezone.utc).timestamp() - ts > max_age_seconds:
            return None, None
        return row["value"], ts
    except Exception as e:
        logging.error(f"app_cache get hatası ({namespace}/{key}): {e}")
        return None, None

def get_namespace(namespace, max_age_seconds=None):
    """{key: value} — max_age_seconds verilirse daha eski kayıtlar dahil edilmez."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            if max_age_seconds is None:
                cur.execute("SELECT key, value FROM app_cache WHERE namespace = %s", (namespace,))
            else:
                cur.execute("SELECT key, value FROM app_cache WHERE namespace = %s AND updated_at > NOW() - make_interval(secs => %s)",
                            (namespace, max_age_seconds))
            return {r["key"]: r["value"] for r in cur.fetchall()}
    except Exception as e:
        logging.error(f"app_cache namespace hatası ({namespace}): {e}")
        return {}

def put(namespace, key, value):
    return put_many(namespace, {key: value})

def put_many(namespace, items):
    if not items:
        return 0
    try:
        from psycopg2.extras import execute_values
        rows = [(namespace, k, json.dumps(v, ensure_ascii=False, default=str)) for k, v in items.items()]
        with _conn() as conn:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO app_cache (namespace, key, value, updated_at) VALUES %s
                    ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """, rows, template="(%s, %s, %s::jsonb, NOW())")
            conn.commit()
        return len(rows)
    except Exception as e:
        logging.error(f"app_cache yazma hatası ({namespace}): {e}")
        return 0

def prune(namespace, older_than_seconds):
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM app_cache WHERE namespace = %s AND updated_at < NOW() - make_interval(secs => %s)",
                            (namespace, older_than_seconds))
                n = cur.rowcount
            conn.commit()
        return n
    except Exception as e:
        logging.error(f"app_cache prune hatası ({namespace}): {e}")
        return 0
