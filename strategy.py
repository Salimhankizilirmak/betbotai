"""
Kupon stratejisi varsayılanları (Faz 2 planında kararlaştırıldı). Kupon oluşturucu (Faz 3) bunları okur.
"""
import os

# --- Futbol kuponu: günde 1, en fazla 5 maç, toplam oran en fazla 8.00 ---
FOOTBALL_MAX_LEGS = 5
FOOTBALL_MAX_TOTAL_ODDS = 8.0
# Kuponda izin verilen marketler (Odds API market key + seçim tipi)
FOOTBALL_COUPON_MARKETS = [
    "h2h",           # 1X2
    "double_chance", # Çifte şans (skor matrisinden türetilir; Odds API'de her lig için yok)
    "totals_1.5",    # 1.5 Alt/Üst
    "totals_2.5",    # 2.5 Alt/Üst
    "btts",          # Karşılıklı gol var/yok
]
# Avrupa kupaları, ileriye dönük takipte kalibrasyon doğrulanana kadar kupona girmez
UEFA_IN_COUPON = os.getenv("UEFA_IN_COUPON", "false").lower() == "true"

# --- NBA kuponu: günde 1, farklı maçlardan 3 oyuncu ---
NBA_PROP_LEGS = 3
NBA_PROP_MARKETS = ["player_points", "player_rebounds", "player_assists"]

# --- Oran toplama ve Odds API kota koruması ---
# Kalan kredi bu değerin altına düşerse toplayıcı yeni istek atmaz (analiz döngüsüne kota bırakır)
ODDS_CREDIT_RESERVE = int(os.getenv("ODDS_CREDIT_RESERVE", "100"))
COLLECT_CLOSING_LINES = os.getenv("COLLECT_CLOSING_LINES", "true").lower() == "true"
# Başlama saatine bu kadar dakika kala alınan oran "kapanış" sayılır (CLV ölçümü için)
CLOSING_WINDOW_MINUTES = int(os.getenv("CLOSING_WINDOW_MINUTES", "15"))
# Kapanış toplamada futbol için sadece kuponda kullanılan marketler çekilir (kredi = market x bölge)
CLOSING_FOOTBALL_MARKETS = "h2h,totals"
PROPS_REGION = "us"

# --- NBA prop bahis eşikleri (models/nba_props_model.py olasılıkları vs marjsız piyasa) ---
PROP_MIN_EDGE = float(os.getenv("PROP_MIN_EDGE", "0.04"))   # model olasılığı - piyasa olasılığı
PROP_MIN_EV = float(os.getenv("PROP_MIN_EV", "0.03"))       # p * oran - 1
PROP_MIN_GAMES = 5
PROP_MIN_MINUTES = 20.0
PROP_ODDS_MIN = 1.50
PROP_ODDS_MAX = 3.00
# Sezon başı / rol değişikliği koruması: model piyasanın bildiği bilgileri (sakatlık, takas, rol) bilmez
PROP_MIN_SEASON_GAMES = int(os.getenv("PROP_MIN_SEASON_GAMES", "3"))  # oyuncunun BU sezonki maç sayısı
PROP_MARKET_SHRINK = float(os.getenv("PROP_MARKET_SHRINK", "0.5"))   # p = piyasa + λ·(model - piyasa)
PROP_MAX_RAW_EDGE = float(os.getenv("PROP_MAX_RAW_EDGE", "0.20"))    # ham fark bundan büyükse eksik bilgi var say

# --- Maç bahisleri (futbol + basketbol): keskin piyasaya (Pinnacle) göre değer (value_bets.py; backtest: reports/football_backtest.json) ---
FOOTBALL_SHARP_BOOK = "pinnacle"
FOOTBALL_MIN_EV = float(os.getenv("FOOTBALL_MIN_EV", "0.02"))
FOOTBALL_ODDS_MIN = 1.30
FOOTBALL_ODDS_MAX = 4.00
# Çok yüksek EV genelde bayat/hatalı oran demektir (bir şirket fiyatı güncellememiş); bahis alınmaz
FOOTBALL_MAX_EV = float(os.getenv("FOOTBALL_MAX_EV", "0.10"))
