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
