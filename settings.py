"""
Çalışma ortamı ayarları: yerel sunucu, Vercel (serverless) ve GitHub Actions (zamanlanmış işler).

- Vercel'de dosya sistemi salt okunur; yazılabilir tek yer /tmp.
- Vercel'de arka plan döngüleri çalışmaz; zamanlanmış işler GitHub Actions'ta jobs.py ile koşar.
"""
import os
import tempfile

IS_VERCEL = bool(os.getenv("VERCEL"))
IS_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"

def _flag(name, default):
    return os.getenv(name, "true" if default else "false").lower() == "true"

WRITABLE_DIR = os.path.join(tempfile.gettempdir(), "betbot") if IS_VERCEL else "."  # Vercel'de /tmp/betbot
DATA_DIR = os.path.join(WRITABLE_DIR, "data")
LOG_DIR = os.path.join(WRITABLE_DIR, "logs")

# Yerel 'python main.py' eski davranış: döngüler uygulama içinde çalışır
RUN_BACKGROUND_LOOPS = _flag("RUN_BACKGROUND_LOOPS", not IS_VERCEL)
# Şema artık supabase/migrations ile kurulur; Vercel'de her soğuk başlangıçta DDL çalıştırmayalım
INIT_DB_ON_IMPORT = _flag("INIT_DB_ON_IMPORT", not IS_VERCEL)
# X'e otomatik paylaşım herkese açık olduğu için açıkça istenmedikçe kapalı
POST_TO_X = _flag("POST_TO_X", False)

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path
