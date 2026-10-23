"""
Günlük kuponlar.

- Futbol: önümüzdeki 24 saatin maçlarından, Pinnacle'a göre değerli seçimlerle (value_bets.book_candidates)
  TEK BİR şirkette oynanabilecek, 2-5 maçlık, toplam oranı <= 8.00 olan ve beklenen değeri en yüksek kupon.
- NBA: projeksiyon modelinin eşiklerini geçen prop'lardan, farklı maçlardan 3 oyuncu, tek şirket.

Kuponlar tekli bahis kasasından ayrı tutulur (sabit stake), Telegram'a gönderilir ve sonuçlandırma işinde kapanır.
Olasılıklar bacakların bağımsız olduğu varsayımıyla çarpılır (aynı maçtan iki seçim alınmaz).
"""
import itertools
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytz

import strategy
from leagues import UEFA_COMPETITIONS

TR = pytz.timezone("Europe/Istanbul")
COUPON_STAKE = 100.0
TOP_PER_BOOK = 12  # kombinasyon araması için şirket başına en iyi N maç

def tr_date(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(TR).date()

def _parse_time(t):
    return datetime.fromisoformat(str(t).replace("Z", "+00:00"))

# ---------------------------------------------------------------- kombinasyon
def best_coupon(legs_by_book, min_legs, max_legs, max_odds=None, top_n=TOP_PER_BOOK):
    """
    legs_by_book: {book: [bacak]}; bacak: group (maç), odds, p (olasılık), ev.
    Her şirket için maç başına en iyi bacak, sonra kısıtlar içinde EV'si en yüksek kombinasyon. Yoksa None.
    """
    best = None
    for book, legs in legs_by_book.items():
        per_group = {}
        for leg in legs:
            g = leg["group"]
            if g not in per_group or leg["ev"] > per_group[g]["ev"]:
                per_group[g] = leg
        cands = sorted(per_group.values(), key=lambda l: -l["ev"])[:top_n]
        for k in range(min_legs, max_legs + 1):
            if len(cands) < k:
                break
            for combo in itertools.combinations(cands, k):
                odds = math.prod(l["odds"] for l in combo)
                if max_odds and odds > max_odds + 1e-9:
                    continue
                p = math.prod(l["p"] for l in combo)
                ev = p * odds - 1
                if best is None or ev > best["ev"] + 1e-12 or (abs(ev - best["ev"]) < 1e-12 and p > best["p"]):
                    best = {"book": book, "legs": sorted(combo, key=lambda l: l.get("commence_time", "")),
                            "odds": odds, "p": p, "ev": ev}
    return best

# ---------------------------------------------------------------- futbol
def football_legs(events, now=None, horizon_hours=24, min_lead_minutes=30):
    import value_bets as vb
    now = now or datetime.now(timezone.utc)
    lo, hi = now + timedelta(minutes=min_lead_minutes), now + timedelta(hours=horizon_hours)
    legs_by_book = defaultdict(list)
    for e in events:
        try:
            ct = _parse_time(e["commence_time"])
        except Exception:
            continue
        if not (lo <= ct <= hi):
            continue
        if not strategy.UEFA_IN_COUPON and e.get("sport_key") in UEFA_COMPETITIONS:
            continue
        for book, cands in vb.book_candidates(e).items():
            for c in cands:
                legs_by_book[book].append({**c, "group": e["id"], "p": c["p_fair"], "event_id": e["id"],
                                           "sport_key": e.get("sport_key"), "home_team": e.get("home_team"),
                                           "away_team": e.get("away_team"), "commence_time": e.get("commence_time")})
    return legs_by_book

def build_football(events, now=None):
    legs = football_legs(events, now)
    c = best_coupon(legs, strategy.FOOTBALL_COUPON_MIN_LEGS, strategy.FOOTBALL_MAX_LEGS,
                    strategy.FOOTBALL_MAX_TOTAL_ODDS)
    return c, {"books_with_value": len(legs), "value_legs": sum(len(v) for v in legs.values()),
               "matches_with_value": len({l["group"] for v in legs.values() for l in v})}

# ---------------------------------------------------------------- NBA
def nba_legs(recs, now=None, min_lead_minutes=15):
    now = now or datetime.now(timezone.utc)
    legs_by_book = defaultdict(list)
    for r in recs:
        try:
            if _parse_time(r["commence_time"]) < now + timedelta(minutes=min_lead_minutes):
                continue
        except Exception:
            continue
        legs_by_book[r.get("book") or "?"].append({
            "group": r["event_id"], "event_id": r["event_id"], "sport_key": "basketball_nba",
            "home_team": r["home_team"], "away_team": r["away_team"], "commence_time": r["commence_time"],
            "player": r["player"], "stat": r["stat"], "side": r["side"], "line": r["line"],
            "pick": r["bet_target"], "bet_target": r["bet_target"], "odds": r["odds"],
            "p": r["p_model"], "p_fair": r["p_fair"], "ev": r["ev"], "book": r.get("book"), "mu": r.get("mu")})
    return legs_by_book

def build_nba(recs, now=None):
    legs = nba_legs(recs, now)
    n = strategy.NBA_PROP_LEGS
    c = best_coupon(legs, n, n, None)
    return c, {"books_with_value": len(legs), "value_legs": sum(len(v) for v in legs.values()),
               "games_with_value": len({l["group"] for v in legs.values() for l in v})}

# ---------------------------------------------------------------- kayıt + Telegram
def save(kind, coupon, now=None):
    """Günde bir kupon (kind, TR günü). Zaten varsa mevcut kaydı döner (tekrar gönderilmez)."""
    from psycopg2.extras import Json
    from bet_manager import get_db_connection
    day = tr_date(now)
    legs = [{k: v for k, v in l.items() if k != "group"} for l in coupon["legs"]]
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO coupons (kind, coupon_date, bookmaker, legs, total_odds, p_fair, ev, stake)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (kind, coupon_date) DO NOTHING RETURNING id
            """, (kind, day, coupon["book"], Json(legs), coupon["odds"], coupon["p"], coupon["ev"], COUPON_STAKE))
            row = cur.fetchone()
            created = row is not None
            if not created:
                cur.execute("SELECT * FROM coupons WHERE kind = %s AND coupon_date = %s", (kind, day))
                row = cur.fetchone()
        conn.commit()
    return dict(row), created

def mark_sent(coupon_id):
    from bet_manager import get_db_connection
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE coupons SET telegram_sent = TRUE WHERE id = %s", (coupon_id,))
        conn.commit()

def _fmt_time(t):
    return _parse_time(t).astimezone(TR).strftime("%d.%m %H:%M")

def _leg_label(leg):
    if leg.get("player"):
        stat_tr = {"PTS": "Sayı", "REB": "Ribaund", "AST": "Asist"}.get(leg["stat"], leg["stat"])
        side_tr = "Üst" if leg["side"] == "OVER" else "Alt"
        return f"{leg['player']} — {stat_tr} {leg['line']} {side_tr}"
    import value_bets as vb
    return vb.pick_label(leg["pick"])

def message(kind, coupon):
    icon, title = ("⚽", "GÜNÜN FUTBOL KUPONU") if kind == "football" else ("🏀", "GÜNÜN NBA KUPONU (3 OYUNCU)")
    lines = [f"{icon} <b>{title}</b> — {tr_date().strftime('%d.%m.%Y')}", f"🏦 <b>Şirket:</b> {coupon['book']}", ""]
    for i, l in enumerate(coupon["legs"], 1):
        prob_label = "Pinnacle adil" if kind == "football" else "model"
        lines.append(f"{i}) 🕒 {_fmt_time(l['commence_time'])} | {l['home_team']} - {l['away_team']}")
        lines.append(f"    ➜ {_leg_label(l)} @ <b>{l['odds']:.2f}</b>  ({prob_label} %{l['p'] * 100:.0f}, EV {l['ev'] * 100:+.1f}%)")
    every = f"yaklaşık {1 / coupon['p']:.0f} kuponda 1" if coupon["p"] > 0 else "-"
    lines += ["",
              f"🎯 <b>Toplam oran:</b> {coupon['odds']:.2f}",
              f"📊 <b>Tutma olasılığı:</b> %{coupon['p'] * 100:.1f} ({every})",
              f"📈 <b>Kupon beklenen değeri:</b> {coupon['ev'] * 100:+.1f}%",
              "",
              f"<i>Sanal kupon, bilgi amaçlıdır. Oranlar {coupon['book']} fiyatlarıdır; İddaa oranları farklı ve genelde "
              f"daha düşüktür, bu yüzden değer orada geçerli olmayabilir. Kayıp serileri normaldir.</i>"]
    return "\n".join(lines)

def empty_message(kind, stats):
    if kind == "football":
        return (f"⚽ <b>Bugün futbol kuponu yok</b> — {tr_date().strftime('%d.%m.%Y')}\n"
                f"Önümüzdeki 24 saatte Pinnacle'a göre değerli seçim olan maç: {stats.get('matches_with_value', 0)}. "
                f"Tek şirkette en az {strategy.FOOTBALL_COUPON_MIN_LEGS} maç ve toplam oran ≤ "
                f"{strategy.FOOTBALL_MAX_TOTAL_ODDS:.2f} şartı sağlanamadı. Değer yoksa kupon zorlanmaz.")
    return (f"🏀 <b>Bugün NBA kuponu yok</b> — {tr_date().strftime('%d.%m.%Y')}\n"
            f"Model eşiklerini geçen prop olan maç: {stats.get('games_with_value', 0)} (3 farklı maç gerekli). "
            f"Sezon başında oyuncuların bu sezon en az {strategy.PROP_MIN_SEASON_GAMES} maçı olması şartı aranır.")

def publish(kind, coupon, stats):
    """Kuponu kaydeder ve (ilk kez oluşturulduysa) Telegram'a gönderir."""
    from bet_manager import send_telegram_message
    if not coupon:
        if strategy.COUPON_SEND_EMPTY:
            send_telegram_message(empty_message(kind, stats))
        return {"coupon": None, **stats}
    row, created = save(kind, coupon)
    if created:
        send_telegram_message(message(kind, coupon))
        mark_sent(row["id"])
    return {"coupon_id": row["id"], "created": created, "legs": len(coupon["legs"]), "book": coupon["book"],
            "total_odds": round(coupon["odds"], 2), "p": round(coupon["p"], 4), "ev": round(coupon["ev"], 4), **stats}

# ---------------------------------------------------------------- sonuçlandırma
def leg_result_football(leg, scores_by_sport):
    """'WON' | 'LOST' | 'VOID' | None (henüz bitmedi)."""
    ev = next((s for s in scores_by_sport.get(leg["sport_key"]) or [] if s.get("id") == leg["event_id"]), None)
    if not ev or not ev.get("completed") or len(ev.get("scores") or []) != 2:
        return None
    sc = {s["name"]: int(s["score"]) for s in ev["scores"]}
    h, a = sc.get(ev.get("home_team")), sc.get(ev.get("away_team"))
    if h is None or a is None:
        return None
    pick = leg["pick"]
    if pick == "HOME_WIN":
        return "WON" if h > a else "LOST"
    if pick == "AWAY_WIN":
        return "WON" if a > h else "LOST"
    if pick == "DRAW":
        return "WON" if h == a else "LOST"
    side, pt = pick.split()
    total, pt = h + a, float(pt)
    if total == pt:
        return "VOID"
    return "WON" if (total > pt) == (side == "OVER") else "LOST"

def leg_result_nba(leg):
    import nba_data
    val = nba_data.get_nba_player_game_stat(leg["player"], leg["commence_time"], leg["stat"])
    if val is None:
        return None
    if val == -999.0 or val == float(leg["line"]):
        return "VOID"
    return "WON" if (val > float(leg["line"])) == (leg["side"] == "OVER") else "LOST"

def coupon_outcome(legs, stake=COUPON_STAKE):
    """(status, profit) — bir bacak kaybettiyse LOST; hepsi bittiyse WON/VOID (iade bacaklar oran 1)."""
    results = [l.get("result") for l in legs]
    if "LOST" in results:
        return "LOST", -stake
    if any(r is None for r in results):
        return "PENDING", 0.0
    won = [l for l in legs if l["result"] == "WON"]
    if not won:
        return "VOID", 0.0
    return "WON", stake * math.prod(l["odds"] for l in won) - stake

async def resolve_pending():
    import asyncio
    from psycopg2.extras import Json
    from bet_manager import get_db_connection, send_telegram_message
    from oddsapi_client import get_scores
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM coupons WHERE status = 'PENDING' ORDER BY coupon_date")
        pending = [dict(r) for r in cur.fetchall()]
    if not pending:
        return {"pending": 0}
    sports = {l["sport_key"] for c in pending if c["kind"] == "football" for l in c["legs"]}
    scores = {s: await get_scores(s) for s in sports}
    done = 0
    for c in pending:
        legs = c["legs"]
        for leg in legs:
            if leg.get("result"):
                continue
            leg["result"] = (await asyncio.to_thread(leg_result_nba, leg)) if c["kind"] == "nba_props" \
                else leg_result_football(leg, scores)
        status, profit = coupon_outcome(legs, c["stake"])
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE coupons SET legs = %s, status = %s, profit = %s, resolved_at = %s WHERE id = %s",
                            (Json(legs), status, profit, datetime.now(timezone.utc) if status != "PENDING" else None, c["id"]))
            conn.commit()
        if status != "PENDING":
            done += 1
            icon = {"WON": "🟢 KUPON TUTTU", "LOST": "🔴 KUPON YATTI", "VOID": "⚪ KUPON İADE"}[status]
            marks = " ".join({"WON": "✅", "LOST": "❌", "VOID": "⚪"}.get(l.get("result"), "⏳") for l in legs)
            send_telegram_message(f"{icon} — {c['coupon_date']:%d.%m} {'futbol' if c['kind'] == 'football' else 'NBA'} "
                                  f"kuponu ({c['total_odds']:.2f})\n{marks}\n💰 {profit:+.2f} BB")
    logging.info(f"🎟️ Kupon sonuçlandırma: {len(pending)} bekleyen, {done} sonuçlandı")
    return {"pending": len(pending), "resolved": done}
