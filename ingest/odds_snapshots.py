"""
Oran snapshot'larını odds_snapshots tablosuna yazar ve kapanış oranlarını toplar.

- Botun zaten yaptığı her Odds API çağrısı (maç oranları, NBA props) ek kredi harcamadan kaydedilir.
- collect_closing_lines(): başlamasına CLOSING_WINDOW_MINUTES kala olan maçların oranlarını
  'closing' olarak kaydeder (CLV ölçümü ve NBA prop backtest'i için).
- Kalan Odds API kredisi ODDS_CREDIT_RESERVE altındaysa toplayıcı istek atmaz.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import strategy
from leagues import ANALYZED_SPORTS

INSERT_SQL = """
    INSERT INTO odds_snapshots (
        captured_at, snapshot_kind, sport_key, event_id, commence_time, home_team, away_team,
        bookmaker, market, selection, player, point, price, bookmaker_last_update
    ) VALUES %s
"""
COLUMNS = ("captured_at", "snapshot_kind", "sport_key", "event_id", "commence_time", "home_team", "away_team",
           "bookmaker", "market", "selection", "player", "point", "price", "bookmaker_last_update")

def parse_time(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

def flatten_event(event, kind="regular", captured_at=None):
    """Tek bir Odds API event'ini (maç veya event-odds yanıtı) satırlara açar."""
    captured_at = captured_at or datetime.now(timezone.utc)
    rows = []
    for bkm in event.get("bookmakers") or []:
        book_update = parse_time(bkm.get("last_update"))
        for market in bkm.get("markets") or []:
            for out in market.get("outcomes") or []:
                price = out.get("price")
                if price is None:
                    continue
                # Props'ta name = Over/Under, description = oyuncu; maç marketlerinde description yok
                rows.append({
                    "captured_at": captured_at,
                    "snapshot_kind": kind,
                    "sport_key": event.get("sport_key"),
                    "event_id": event.get("id"),
                    "commence_time": parse_time(event.get("commence_time")),
                    "home_team": event.get("home_team"),
                    "away_team": event.get("away_team"),
                    "bookmaker": bkm.get("key"),
                    "market": market.get("key"),
                    "selection": out.get("name"),
                    "player": out.get("description"),
                    "point": out.get("point"),
                    "price": float(price),
                    "bookmaker_last_update": parse_time(market.get("last_update")) or book_update,
                })
    return rows

def flatten_events(events, kind="regular"):
    captured_at = datetime.now(timezone.utc)
    if isinstance(events, dict):
        events = [events]
    rows = []
    for e in events or []:
        rows.extend(flatten_event(e, kind, captured_at))
    return rows

# Sadece kuponda kullanılan marketler saklanır (spreads vb. atlanır)
STORED_MARKETS = {"h2h", "totals", *strategy.NBA_PROP_MARKETS}

# Son fiyat: (event, şirket, market, seçim, oyuncu, çizgi) -> fiyat. Her GitHub Actions işi yeni bir süreç
# olduğu için bellek tek başına yetmez; bir event'in son fiyatları ilk görüldüğünde DB'den yüklenir.
_last_price = {}
_loaded_events = set()

def _key(r):
    point = None if r["point"] is None else round(float(r["point"]), 2)
    return (r["event_id"], r["bookmaker"], r["market"], r["selection"], r["player"], point)

def load_last_prices(event_ids):
    """Bu süreçte henüz yüklenmemiş event'lerin DB'deki son 'regular' fiyatlarını _last_price'a alır."""
    todo = [e for e in set(event_ids) if e and e not in _loaded_events]
    if not todo:
        return 0
    try:
        from bet_manager import get_db_connection
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (event_id, bookmaker, market, selection, player, point)
                       event_id, bookmaker, market, selection, player, point, price
                FROM odds_snapshots WHERE snapshot_kind = 'regular' AND event_id = ANY(%s)
                ORDER BY event_id, bookmaker, market, selection, player, point, captured_at DESC
            """, (todo,))
            for r in cur.fetchall():
                _last_price.setdefault(_key(r), round(float(r["price"]), 3))
        _loaded_events.update(todo)
        return len(todo)
    except Exception as e:
        logging.error(f"son fiyatlar yüklenemedi: {e}")
        return 0

def filter_changed(rows):
    """
    'regular' snapshot'larda fiyatı değişmeyen satırları atar (DB şişmesin);
    'closing' snapshot'lar her zaman tam kaydedilir. (REAL kolon float32: fiyatlar 3 haneye yuvarlanıp karşılaştırılır)
    """
    out = []
    for r in rows:
        if r["market"] not in STORED_MARKETS:
            continue
        key, price = _key(r), round(float(r["price"]), 3)
        if r["snapshot_kind"] == "regular" and _last_price.get(key) == price:
            continue
        _last_price[key] = price
        out.append(r)
    return out

def save_rows(rows):
    """Satırları toplu yazar. Hata durumunda loglar ve 0 döner (bot akışını asla bozmaz)."""
    if not rows:
        return 0
    try:
        from psycopg2.extras import execute_values
        from bet_manager import get_db_connection
        values = [tuple(r[c] for c in COLUMNS) for r in rows]
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                execute_values(cur, INSERT_SQL, values, page_size=500)
            conn.commit()
        return len(rows)
    except Exception as e:
        logging.error(f"odds_snapshots yazma hatası: {e}")
        return 0

async def record_odds_response(events, kind="regular"):
    """Async çağıranlar için: DB yazımını thread'de yapar."""
    rows = flatten_events(events, kind)
    if kind == "regular":
        await asyncio.to_thread(load_last_prices, [r["event_id"] for r in rows])
    rows = filter_changed(rows)
    if not rows:
        return 0
    return await asyncio.to_thread(save_rows, rows)

PRUNE_SQL = """
    DELETE FROM odds_snapshots s
    USING (
        SELECT id, row_number() OVER (
            PARTITION BY event_id, bookmaker, market, selection, player, point ORDER BY captured_at
        ) AS rn
        FROM odds_snapshots
        WHERE snapshot_kind = 'regular' AND commence_time < NOW() - make_interval(days => %s)
    ) x
    WHERE s.id = x.id AND x.rn > 1
"""

def prune_snapshots(older_than_days=2):
    """
    Başlamasının üzerinden older_than_days geçmiş maçlarda her oran için sadece ilk (açılış) 'regular'
    snapshot'ı ve 'closing' snapshot'ları tutar; aradaki hareketleri siler (Supabase ücretsiz kotası için).
    """
    try:
        from bet_manager import get_db_connection
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(PRUNE_SQL, (older_than_days,))
                n = cur.rowcount
            conn.commit()
        if n:
            logging.info(f"🧹 odds_snapshots: {n} ara snapshot silindi")
        return n
    except Exception as e:
        logging.error(f"odds_snapshots prune hatası: {e}")
        return 0

def events_closing_soon(events, now=None, window_minutes=None):
    """Başlamasına window_minutes veya daha az kalmış, henüz başlamamış maçlar."""
    now = now or datetime.now(timezone.utc)
    window = timedelta(minutes=window_minutes or strategy.CLOSING_WINDOW_MINUTES)
    out = []
    for e in events or []:
        ct = parse_time(e.get("commence_time"))
        if ct and now < ct <= now + window:
            out.append(e)
    return out

def _events_with_closing(event_ids):
    """Bu event'lerden hangileri için zaten 'closing' snapshot alınmış?"""
    if not event_ids:
        return set()
    try:
        from bet_manager import get_db_connection
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT event_id, market FROM odds_snapshots WHERE snapshot_kind = 'closing' AND event_id = ANY(%s)",
                    (list(event_ids),),
                )
                return {(r["event_id"], r["market"]) for r in cur.fetchall()}
    except Exception as e:
        logging.error(f"closing kontrol hatası: {e}")
        return set()

def credits_available():
    from oddsapi_client import CREDIT_STATE
    remaining = CREDIT_STATE.get("remaining")
    return remaining is None or remaining >= strategy.ODDS_CREDIT_RESERVE

async def collect_closing_lines(sports=None):
    """
    Her lig için (önbellekten) yaklaşan maç listesine bakar; kapanış penceresine giren ve henüz
    kapanışı alınmamış maç varsa o ligin oranlarını taze çeker ve sadece o maçları 'closing' kaydeder.
    NBA maçları için ayrıca oyuncu prop kapanışları alınır.
    """
    from oddsapi_client import get_odds, get_event_odds
    sports = sports or ANALYZED_SPORTS
    saved = 0
    for sport in sports:
        if not credits_available():
            logging.warning("Odds API kredisi rezervin altında, kapanış toplama durduruldu.")
            break
        try:
            listing = await get_odds(sport)  # önbellekli liste: ek kredi harcamaz
            soon = events_closing_soon(listing if isinstance(listing, list) else [])
            if not soon:
                continue
            done = await asyncio.to_thread(_events_with_closing, [e["id"] for e in soon])
            pending_ids = {e["id"] for e in soon if (e["id"], "h2h") not in done}
            if pending_ids:
                fresh = await get_odds(sport, markets=strategy.CLOSING_FOOTBALL_MARKETS, force_refresh=True, record=False)
                closing = [e for e in (fresh or []) if e.get("id") in pending_ids]
                saved += await record_odds_response(closing, kind="closing")

            if sport == "basketball_nba":
                for e in soon:
                    if (e["id"], "player_points") in done or not credits_available():
                        continue
                    props = await get_event_odds(sport, e["id"], ",".join(strategy.NBA_PROP_MARKETS),
                                                 region=strategy.PROPS_REGION, record=False)
                    if props:
                        saved += await record_odds_response(props, kind="closing")
        except Exception as ex:
            logging.error(f"Kapanış toplama hatası ({sport}): {ex}")
    if saved:
        logging.info(f"📸 Kapanış oranları kaydedildi: {saved} satır")
    return saved
