"""
Öğrenme döngüsü (haftalık): ölç -> raporla -> korumalı otomatik ayar.

Ölçüm
- CLV (kapanış oranını yenme): her değer seçiminin (predictions: bot her turda değerlendirdiği maçın en iyi
  adayını kaydeder) aldığı fiyat x Pinnacle KAPANIŞ marjsız olasılığı - 1. Şanstan bağımsız, hızlı biriken sinyal.
  Bahis alınmayan seçimler de sayılır; kapatılan bir segment için veri birikmeye devam eder.
- Gerçek bahisler: isabet vs beklenen (Pinnacle olasılığı), z-skoru, ROI; kuponlar.
- Segmentler: lig, market, bahis şirketi, oran aralığı.

Otomatik ayar (korumalı; app_cache 'learning'/'overrides')
- Bir segmentte en az SEG_MIN CLV örneği varsa ve CLV'nin %90 güven aralığının ÜST sınırı < 0 ise o segment
  bahisten çıkarılır (analiz/kayıt devam eder). Kapalı segmentin ortalama CLV'si > 0 olursa tekrar açılır.
- EV eşiği: [%2, %3) bandındaki seçimlerin CLV'si (en az 100 örnek) güvenle negatifse eşik %3'e çıkar; band
  pozitife dönerse %2'ye iner. Sınırlar: %2-%5.
- Kazan/kaybet sonucuna göre ayar YAPILMAZ: 50 bahiste kâr/zarar büyük ölçüde şanstır.

Güvenli mod (stake çarpanı): son değer bahislerinde gerçek isabet beklenenden anlamlı düşükse (z < -2) veya
son bahislerin ortalama CLV'si < -%2 ise stake yarıya iner. (Eski kural: "başarı oranı < %50" — ortalama oran
~2.5 iken bu normaldir ve yanlış ölçüttür.)
"""
import json
import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import strategy
from backtest import metrics
from market.devig import devig

SEG_MIN = 50          # otomatik ayar için segment başına en az CLV örneği
REPORT_MIN = 10       # raporda gösterilecek segment için en az örnek
EV_BAND_MIN = 100     # EV eşiği ayarı için en az örnek
LOOKBACK_DAYS = 90
ODDS_BUCKETS = ((1.30, 1.60), (1.60, 2.00), (2.00, 2.50), (2.50, 3.20), (3.20, 4.01))
NAMESPACE = "learning"

def _db():
    from bet_manager import get_db_connection
    return get_db_connection()

def odds_bucket(odds):
    for lo, hi in ODDS_BUCKETS:
        if lo <= odds < hi:
            return f"{lo:.2f}-{min(hi, 4.0):.2f}"
    return "<1.30" if odds < ODDS_BUCKETS[0][0] else ">4.00"

def league_of(sport_key):
    return (sport_key or "?").replace("soccer_", "").replace("basketball_", "")

# ---------------------------------------------------------------- Pinnacle kapanış
def pinnacle_close(event_ids):
    """
    {event_id: {"h2h": {selection: price}, "totals": {point: {"Over": p, "Under": p}}, "home": .., "away": ..}}
    Önce 'closing' snapshot; yoksa başlama saatinden önceki son 'regular' Pinnacle fiyatı.
    """
    if not event_ids:
        return {}
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (event_id, market, selection, point)
                   event_id, market, selection, point, price, home_team, away_team
            FROM odds_snapshots
            WHERE bookmaker = 'pinnacle' AND event_id = ANY(%s) AND market IN ('h2h', 'totals')
              AND (snapshot_kind = 'closing' OR captured_at <= commence_time)
            ORDER BY event_id, market, selection, point, (snapshot_kind = 'closing') DESC, captured_at DESC
        """, (list(event_ids),))
        rows = cur.fetchall()
    out = {}
    for r in rows:
        e = out.setdefault(r["event_id"], {"h2h": {}, "totals": defaultdict(dict), "home": r["home_team"], "away": r["away_team"]})
        if r["market"] == "h2h":
            e["h2h"][r["selection"]] = float(r["price"])
        elif r["point"] is not None:
            e["totals"][round(float(r["point"]), 2)][r["selection"]] = float(r["price"])
    return out

def close_fair(pick, close):
    """Seçimin Pinnacle kapanış marjsız olasılığı; veri yoksa None."""
    if not close:
        return None
    if pick in ("HOME_WIN", "DRAW", "AWAY_WIN"):
        h = close["h2h"]
        names = {"HOME_WIN": close["home"], "AWAY_WIN": close["away"], "DRAW": "Draw"}
        keys = [names["HOME_WIN"], "Draw", names["AWAY_WIN"]] if "Draw" in h else [names["HOME_WIN"], names["AWAY_WIN"]]
        if not all(k in h for k in keys) or names[pick] not in keys:
            return None
        fair = devig([h[k] for k in keys], "shin" if len(keys) == 3 else "power")
        return fair[keys.index(names[pick])]
    try:
        side, pt = pick.split()
        t = close["totals"].get(round(float(pt), 2), {})
        if "Over" in t and "Under" in t:
            p = devig([t["Over"], t["Under"]], "power")
            return p[0] if side.upper() == "OVER" else p[1]
    except ValueError:
        pass
    return None

# ---------------------------------------------------------------- veri
def load_value_picks(days=LOOKBACK_DAYS):
    """Başlamış maçların değer seçimleri (EV >= eşik), (maç, seçim, şirket) başına ilk kayıt."""
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (event_id, selection, bookmaker)
                   event_id, sport_key, commence_time, market, selection, bookmaker, price, p_market_fair, edge, created_at
            FROM predictions
            WHERE model_name IN ('value_vs_pinnacle', 'football_value')
              AND edge >= %s AND edge <= %s AND commence_time < NOW() AND created_at > NOW() - make_interval(days => %s)
            ORDER BY event_id, selection, bookmaker, created_at
        """, (strategy.FOOTBALL_MIN_EV, strategy.FOOTBALL_MAX_EV, days))
        return [dict(r) for r in cur.fetchall()]

def load_bets(days=LOOKBACK_DAYS):
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM bets WHERE created_at > NOW() - make_interval(days => %s)", (days,))
        return [dict(r) for r in cur.fetchall()]

def attach_clv(rows, price_key, pick_key, event_key):
    closes = pinnacle_close({r[event_key] for r in rows})
    for r in rows:
        f = close_fair(r[pick_key], closes.get(r[event_key]))
        r["clv"] = (float(r[price_key]) * f - 1) if f else None
    return rows

# ---------------------------------------------------------------- istatistik
def clv_stats(rows):
    clvs = [r["clv"] for r in rows if r.get("clv") is not None]
    if not clvs:
        return {"n": 0}
    lo, hi = metrics.bootstrap_ci(clvs) if len(clvs) >= 5 else (float("nan"), float("nan"))
    return {"n": len(clvs), "avg": sum(clvs) / len(clvs), "ci90": [lo, hi], "positive_share": sum(c > 0 for c in clvs) / len(clvs)}

def bet_stats(bets):
    res = [b for b in bets if b["status"] in ("WON", "LOST")]
    if not res:
        return {"n": 0}
    wins = sum(b["status"] == "WON" for b in res)
    ps = [float(b["p_fair"]) for b in res if b.get("p_fair") is not None]
    out = {"n": len(res), "wins": wins, "hit": wins / len(res), "staked": sum(b["bet_amount"] for b in res),
           "profit": sum(b["profit"] for b in res)}
    out["roi"] = out["profit"] / out["staked"] if out["staked"] else 0
    if len(ps) == len(res):
        exp = sum(ps)
        var = sum(p * (1 - p) for p in ps)
        out.update(expected_hit=exp / len(res), z=(wins - exp) / math.sqrt(var) if var > 0 else 0.0)
    return out

def by_segment(rows, key_fn, stat_fn, min_n=REPORT_MIN):
    groups = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    out = {k: stat_fn(v) for k, v in groups.items()}
    return {k: v for k, v in sorted(out.items(), key=lambda kv: -kv[1].get("n", 0)) if v.get("n", 0) >= min_n}

SEGMENTS = {
    "league": lambda r: league_of(r["sport_key"]),
    "market": lambda r: r.get("market") or "?",
    "bookmaker": lambda r: r.get("bookmaker") or "?",
    "odds": lambda r: odds_bucket(float(r.get("price") or r.get("odds_value") or 0)),
}

def calibration(bets):
    res = [b for b in bets if b["status"] in ("WON", "LOST") and b.get("p_fair") is not None]
    if not res:
        return []
    return metrics.calibration_table([float(b["p_fair"]) for b in res], [int(b["status"] == "WON") for b in res], bins=5)

def build_report(days=LOOKBACK_DAYS):
    picks = attach_clv(load_value_picks(days), "price", "selection", "event_id")
    bets = attach_clv(load_bets(days), "odds_value", "bet_target", "match_id")
    value_bets = [b for b in bets if b.get("method") == "value_vs_pinnacle"]
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT kind, status, count(*) n, coalesce(sum(profit), 0) profit FROM coupons GROUP BY 1, 2")
        coupons = [dict(r) for r in cur.fetchall()]
    ev_low = [p for p in picks if p["edge"] < 0.03]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "days": days,
        "picks_clv": clv_stats(picks),
        "picks_clv_by": {name: by_segment(picks, fn, clv_stats) for name, fn in SEGMENTS.items()},
        "ev_band_2_3": clv_stats(ev_low), "ev_band_3_plus": clv_stats([p for p in picks if p["edge"] >= 0.03]),
        "bets_by_method": {m: {**bet_stats([b for b in bets if (b.get("method") or "llm_legacy") == m]),
                               "clv": clv_stats([b for b in bets if (b.get("method") or "llm_legacy") == m])}
                           for m in sorted({b.get("method") or "llm_legacy" for b in bets})},
        "value_bets_by": {name: by_segment(value_bets, fn, bet_stats, min_n=5) for name, fn in SEGMENTS.items()},
        "calibration": calibration(value_bets),
        "coupons": coupons,
    }

# ---------------------------------------------------------------- otomatik ayar
_overrides_cache = {"v": None, "ts": 0.0}

def get_overrides(max_age=3600):
    """Bahis kararında kullanılan ayarlar (value_bets bunu okur). Hata olursa boş (hiçbir şey kapatılmaz)."""
    if _overrides_cache["v"] is None or time.time() - _overrides_cache["ts"] > max_age:
        try:
            import cache_store
            v, _ = cache_store.get(NAMESPACE, "overrides")
        except Exception:
            v = None
        _overrides_cache.update(v=v or {}, ts=time.time())
    return _overrides_cache["v"]

def is_blocked(sport_key, market, bookmaker, overrides=None):
    o = overrides if overrides is not None else get_overrides()
    b = o.get("blocked") or {}
    return (league_of(sport_key) in b.get("league", []) or market in b.get("market", [])
            or bookmaker in b.get("bookmaker", []))

def min_ev(overrides=None):
    o = overrides if overrides is not None else get_overrides()
    return max(strategy.FOOTBALL_MIN_EV, min(0.05, float(o.get("min_ev") or strategy.FOOTBALL_MIN_EV)))

def decide_overrides(report, prev):
    """Yeni ayarlar + değişiklik listesi. Sadece yeterli örnekli segmentlerde, CLV güven aralığına göre."""
    prev = prev or {}
    blocked = {dim: set((prev.get("blocked") or {}).get(dim, [])) for dim in ("league", "market", "bookmaker")}
    changes = []
    for dim in blocked:
        for seg, st in (report["picks_clv_by"].get(dim) or {}).items():
            if st.get("n", 0) < SEG_MIN:
                continue
            hi = st["ci90"][1]
            if seg not in blocked[dim] and hi < 0:
                blocked[dim].add(seg)
                changes.append(f"🚫 {dim}={seg} kapatıldı (CLV {st['avg'] * 100:+.1f}%, GA üst {hi * 100:+.1f}%, n={st['n']})")
            elif seg in blocked[dim] and st["avg"] > 0:
                blocked[dim].discard(seg)
                changes.append(f"✅ {dim}={seg} tekrar açıldı (CLV {st['avg'] * 100:+.1f}%, n={st['n']})")
    cur_min = float(prev.get("min_ev") or strategy.FOOTBALL_MIN_EV)
    band = report["ev_band_2_3"]
    new_min = cur_min
    if band.get("n", 0) >= EV_BAND_MIN:
        if band["ci90"][1] < 0:
            new_min = 0.03
        elif band["avg"] > 0:
            new_min = strategy.FOOTBALL_MIN_EV
    new_min = max(strategy.FOOTBALL_MIN_EV, min(0.05, new_min))
    if abs(new_min - cur_min) > 1e-9:
        changes.append(f"🎚️ EV eşiği %{cur_min * 100:.0f} → %{new_min * 100:.0f} (%2-3 bandı CLV {band['avg'] * 100:+.1f}%, n={band['n']})")
    return {"blocked": {k: sorted(v) for k, v in blocked.items()}, "min_ev": new_min,
            "updated_at": datetime.now(timezone.utc).isoformat()}, changes

# ---------------------------------------------------------------- güvenli mod
_mult_cache = {"v": None, "ts": 0.0}

def stake_multiplier(max_age=3600):
    """1.0 veya 0.5 — son değer bahislerinde gerçek isabet beklenenden anlamlı düşükse ya da CLV negatifse."""
    if _mult_cache["v"] is not None and time.time() - _mult_cache["ts"] < max_age:
        return _mult_cache["v"]
    mult = 1.0
    try:
        bets = [b for b in load_bets(60) if b.get("method") == "value_vs_pinnacle"]
        res = sorted([b for b in bets if b["status"] in ("WON", "LOST")], key=lambda b: b["created_at"])[-60:]
        st = bet_stats(res)
        clv = clv_stats(attach_clv(res[-50:], "odds_value", "bet_target", "match_id"))
        if st.get("n", 0) >= 30 and st.get("z", 0) < -2:
            mult = 0.5
            logging.warning(f"GÜVENLİ MOD: isabet %{st['hit'] * 100:.0f}, beklenen %{st['expected_hit'] * 100:.0f} (z={st['z']:.1f})")
        elif clv.get("n", 0) >= 30 and clv["avg"] < -0.02:
            mult = 0.5
            logging.warning(f"GÜVENLİ MOD: son bahislerin ortalama CLV'si {clv['avg'] * 100:+.1f}%")
    except Exception as e:
        logging.error(f"güvenli mod hesaplanamadı: {e}")
    _mult_cache.update(v=mult, ts=time.time())
    return mult

# ---------------------------------------------------------------- rapor metni + çalıştırma
def _pct(x):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.1f}%"

def report_message(r, changes, overrides):
    lines = [f"📚 <b>Haftalık öğrenme raporu</b> — {datetime.now(timezone.utc):%d.%m.%Y} (son {r['days']} gün)", ""]
    vb = r["bets_by_method"].get("value_vs_pinnacle", {})
    if vb.get("n"):
        exp = f", beklenen %{vb['expected_hit'] * 100:.0f} (z={vb['z']:+.1f})" if "expected_hit" in vb else ""
        lines.append(f"🎯 <b>Değer bahisleri:</b> {vb['n']} sonuçlandı, isabet %{vb['hit'] * 100:.0f}{exp}, "
                     f"ROI {_pct(vb['roi'])}, kâr {vb['profit']:+.0f} BB")
    for m, st in r["bets_by_method"].items():
        if m != "value_vs_pinnacle" and st.get("n"):
            lines.append(f"   • {m}: {st['n']} bahis, isabet %{st['hit'] * 100:.0f}, ROI {_pct(st['roi'])}")
    c = r["picks_clv"]
    if c.get("n"):
        verdict = "kapanışı YENİYORUZ" if c["ci90"][0] > 0 else "kapanışı yenemiyoruz" if c["ci90"][1] < 0 else "henüz belirsiz"
        lines.append(f"📈 <b>CLV</b> (tüm değer seçimleri, n={c['n']}): ortalama {_pct(c['avg'])} "
                     f"[GA {_pct(c['ci90'][0])}…{_pct(c['ci90'][1])}], %{c['positive_share'] * 100:.0f} pozitif → {verdict}")
    else:
        lines.append("📈 CLV: henüz kapanış oranı olan seçim yok")
    for dim, title in (("league", "Lig"), ("market", "Market"), ("bookmaker", "Şirket"), ("odds", "Oran aralığı")):
        segs = r["picks_clv_by"].get(dim) or {}
        if segs:
            top = list(segs.items())[:6]
            lines.append(f"• <b>{title}</b> (CLV): " + " | ".join(f"{k} {_pct(v['avg'])} (n={v['n']})" for k, v in top))
    cal = r["calibration"]
    if cal:
        lines.append("🧪 <b>Kalibrasyon</b> (Pinnacle olasılığı → gerçekleşen): " +
                     " | ".join(f"%{x['mean_p'] * 100:.0f}→%{x['freq'] * 100:.0f} (n={x['n']})" for x in cal))
    won = sum(x["n"] for x in r["coupons"] if x["status"] == "WON")
    total = sum(x["n"] for x in r["coupons"] if x["status"] in ("WON", "LOST"))
    if total:
        lines.append(f"🎟️ Kuponlar: {won}/{total} tuttu, kâr {sum(x['profit'] for x in r['coupons']):+.0f} BB")
    lines.append("")
    lines.append("⚙️ <b>Otomatik ayarlar:</b> " + ("; ".join(changes) if changes else
                 f"değişiklik yok (bir segmentin ayarlanması için en az {SEG_MIN} CLV örneği gerekir)"))
    blocked = [f"{d}={s}" for d, v in (overrides.get("blocked") or {}).items() for s in v]
    lines.append(f"Kapalı segmentler: {', '.join(blocked) if blocked else 'yok'} · EV eşiği %{overrides['min_ev'] * 100:.0f}")
    lines.append("<i>Ayarlar kazanç/kayba değil CLV'ye göre yapılır; küçük örnekte sonuçlar şansa bağlıdır.</i>")
    return "\n".join(lines)

def run(send=True):
    import cache_store
    from bet_manager import send_telegram_message
    report = build_report()
    prev, _ = cache_store.get(NAMESPACE, "overrides")
    overrides, changes = decide_overrides(report, prev)
    cache_store.put(NAMESPACE, "overrides", overrides)
    cache_store.put(NAMESPACE, "last_report", {"report": report, "changes": changes, "overrides": overrides})
    _overrides_cache.update(v=overrides, ts=time.time())
    if send:
        send_telegram_message(report_message(report, changes, overrides))
    logging.info(f"📚 öğrenme: CLV n={report['picks_clv'].get('n')}, değişiklik {len(changes)}")
    return {"picks_clv": report["picks_clv"], "changes": changes, "overrides": overrides}
