"""
Maç bahsi kararı (futbol + basketbol): keskin piyasaya (Pinnacle) göre değer.

    adil olasılık p  = Pinnacle oranlarının marjı çıkarılmış hali (1X2: Shin; 2 seçenekli taraf ve alt/üst: power)
    fiyat            = diğer şirketler arasındaki en yüksek oran (borsalar hariç: komisyon öncesi fiyat)
    EV               = p · fiyat − 1      ->  EV >= FOOTBALL_MIN_EV ve 1.30 <= fiyat <= 4.00 ise bahis

Backtest (futbol, reports/football_backtest.json, value_vs_pinnacle): EV >= %2'de ROI +%7.9, CLV +%3.6.
Basketbol için geçmiş oran verisi yok; aynı kural uygulanır ve predictions + kapanış oranlarıyla izlenir.
Kendi modelimiz (Dixon-Coles) piyasayı geçemediği için karara katılmaz; LLM sadece gerekçe metni yazar.
"""
from collections import defaultdict

import learning
import strategy
from market.devig import devig

EXCHANGES = {"betfair_ex_eu", "betfair_ex_uk", "betfair_ex_au", "matchbook", "smarkets"}

def _norm(name):
    return str(name).strip().lower()

def _outcomes_1x2(event):
    """{bookmaker: {'HOME_WIN': price, ('DRAW': price,) 'AWAY_WIN': price}} — futbolda 3, basketbolda 2 seçenek."""
    home, away = _norm(event.get("home_team")), _norm(event.get("away_team"))
    n_outcomes = 3 if str(event.get("sport_key", "")).startswith("soccer") else 2
    out = {}
    for b in event.get("bookmakers") or []:
        for m in b.get("markets") or []:
            if m.get("key") != "h2h":
                continue
            sel = {}
            for o in m.get("outcomes") or []:
                n = _norm(o.get("name"))
                key = "HOME_WIN" if n == home else "AWAY_WIN" if n == away else "DRAW" if n == "draw" else None
                if key and o.get("price"):
                    sel[key] = float(o["price"])
            if len(sel) == n_outcomes:
                out[b["key"]] = sel
    return out

def _outcomes_totals(event):
    """{point: {bookmaker: {'OVER': price, 'UNDER': price}}}"""
    out = defaultdict(dict)
    for b in event.get("bookmakers") or []:
        for m in b.get("markets") or []:
            if m.get("key") != "totals":
                continue
            by_point = defaultdict(dict)
            for o in m.get("outcomes") or []:
                side = _norm(o.get("name"))
                if side in ("over", "under") and o.get("price") and o.get("point") is not None:
                    by_point[float(o["point"])][side.upper()] = float(o["price"])
            for pt, sel in by_point.items():
                if len(sel) == 2:
                    out[pt][b["key"]] = sel
    return out

def _best(books, outcome, sharp):
    best = None
    for book, sel in books.items():
        if book == sharp or book in EXCHANGES or outcome not in sel:
            continue
        if best is None or sel[outcome] > best[0]:
            best = (sel[outcome], book)
    return best

def evaluate_event(event, sharp=None):
    """
    Bir maçın tüm adayları ve en iyi seçimi. Pinnacle yoksa None (referans yok).
    Döner: {"candidates": [...], "best": aday veya None, "sharp_book": ..., "n_books": ...}
    """
    sharp = sharp or strategy.FOOTBALL_SHARP_BOOK
    cands = []
    h2h = _outcomes_1x2(event)
    if sharp in h2h:
        names = ("HOME_WIN", "DRAW", "AWAY_WIN") if "DRAW" in h2h[sharp] else ("HOME_WIN", "AWAY_WIN")
        fair = devig([h2h[sharp][n] for n in names], "shin" if len(names) == 3 else "power")
        for n, p in zip(names, fair):
            b = _best(h2h, n, sharp)
            if b:
                cands.append({"market": "h2h", "pick": n, "bet_target": n, "p_fair": p, "odds": b[0], "book": b[1],
                              "sharp_odds": h2h[sharp][n], "ev": p * b[0] - 1})
    for pt, books in _outcomes_totals(event).items():
        if sharp not in books:
            continue
        fair = devig([books[sharp]["OVER"], books[sharp]["UNDER"]], "power")
        for side, p in zip(("OVER", "UNDER"), fair):
            b = _best(books, side, sharp)
            if b:
                cands.append({"market": "totals", "pick": f"{side} {pt:g}", "bet_target": f"{side} {pt:g}", "p_fair": p,
                              "odds": b[0], "book": b[1], "sharp_odds": books[sharp][side], "ev": p * b[0] - 1})
    if not cands:
        return None
    # Tekli bahis: "yüksek isabet modu" oran aralığı (SINGLE_ODDS_*); kupon bacakları book_candidates'ta FOOTBALL_ODDS_*
    # learning.py: EV eşiği ve CLV'si güvenle negatif segmentler (lig/market/şirket) haftalık ayarlanır
    overrides = learning.get_overrides()
    floor = learning.min_ev(overrides)
    ok = [c for c in cands if floor <= c["ev"] <= strategy.FOOTBALL_MAX_EV
          and strategy.SINGLE_ODDS_MIN <= c["odds"] <= strategy.SINGLE_ODDS_MAX
          and not learning.is_blocked(event.get("sport_key"), c["market"], c["book"], overrides)]
    return {"candidates": cands, "best": max(ok, key=lambda c: c["ev"]) if ok else None,
            "top": max(cands, key=lambda c: c["ev"]), "sharp_book": sharp,
            "n_books": len({b.get("key") for b in event.get("bookmakers") or []})}

PICK_TR = {"HOME_WIN": "Ev sahibi kazanır (1)", "DRAW": "Beraberlik (X)", "AWAY_WIN": "Deplasman kazanır (2)"}

def pick_label(pick):
    if pick in PICK_TR:
        return PICK_TR[pick]
    side, pt = pick.split()
    return f"{pt} {'Üst' if side == 'OVER' else 'Alt'}"

def summary_text(event, res):
    """Karar özeti (LLM'siz, her zaman doğru rakamlarla)."""
    c = res["best"] or res["top"]
    if res["best"]:
        verdict = "DEĞER VAR"
    elif c["ev"] >= strategy.FOOTBALL_MIN_EV:
        verdict = f"değer var ama oran {strategy.SINGLE_ODDS_MIN:.2f}-{strategy.SINGLE_ODDS_MAX:.2f} tekli bahis aralığı dışında"
    else:
        verdict = "değer yok"
    return (f"{pick_label(c['pick'])}: Pinnacle marjsız olasılık %{c['p_fair'] * 100:.1f} "
            f"(Pinnacle oranı {c['sharp_odds']:.2f}), en iyi oran {c['odds']:.2f} ({c['book']}) → "
            f"beklenen değer {c['ev'] * 100:+.1f}% [{verdict}; eşik +%{strategy.FOOTBALL_MIN_EV * 100:.0f}, "
            f"{res['n_books']} şirket].")

def to_analysis(event, res, explanation=None, facts_text=None):
    """Panel/AI_CACHE ve place_virtual_bet ile uyumlu analiz sözlüğü: karar özeti + gerçek veriler + analiz."""
    c = res["best"] or res["top"]
    p = round(c["p_fair"] * 100)
    text = summary_text(event, res)
    if facts_text:
        text = f"{text}\n\n{facts_text}"
    if explanation:
        text = f"{text}\n\n🧠 {explanation}"
    return {
        "bet_target": c["bet_target"] if res["best"] else "N/A",
        "odds_value": c["odds"],
        "win_probability": p,
        "risk_score": 100 - p,
        "recommendation": pick_label(c["pick"]) if res["best"] else "Değer yok",
        "analysis": text,
        "is_recommended": bool(res["best"]),
        "method": "value_vs_pinnacle",
        "ev": round(c["ev"], 4),
        "p_fair": round(c["p_fair"], 4),
        "bookmaker": c["book"],
        "sharp_odds": c["sharp_odds"],
        "market": c["market"],
        "facts_text": facts_text,
        "explanation": explanation,
    }

def record_predictions(rows):
    """Değerlendirilen her maçın en iyi adayını predictions tablosuna yazar (sonradan CLV/isabet ölçümü)."""
    if not rows:
        return
    try:
        from psycopg2.extras import execute_values, Json
        from bet_manager import get_db_connection
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO predictions (model_name, model_version, sport_key, event_id, commence_time, market,
                        selection, p_model, p_market_fair, price, bookmaker, edge, meta) VALUES %s
                """, [("value_vs_pinnacle", "pinnacle-v1", e.get("sport_key"), e.get("id"), e.get("commence_time"),
                       c["market"], c["pick"], c["p_fair"], c["p_fair"], c["odds"], c["book"], c["ev"],
                       Json({"sharp_odds": c["sharp_odds"], "bet": bool(r["best"]), "n_books": r["n_books"]}))
                      for e, r in rows for c in [r["best"] or r["top"]]])
            conn.commit()
    except Exception as ex:
        import logging
        logging.error(f"value predictions yazma hatası: {ex}")

def book_candidates(event, sharp=None):
    """
    Kupon için: her şirketin KENDİ fiyatıyla değerli seçimleri (bir kupon tek şirkette oynanır).
    Döner: {book: [aday, ...]}; adaylar EV eşiği/üst sınırı ve oran aralığını geçmiş, maç başına tümü.
    """
    sharp = sharp or strategy.FOOTBALL_SHARP_BOOK
    out = defaultdict(list)
    overrides = learning.get_overrides()
    floor = learning.min_ev(overrides)

    def add(book, market, pick, p, odds, sharp_odds):
        ev = p * odds - 1
        if (floor <= ev <= strategy.FOOTBALL_MAX_EV
                and strategy.FOOTBALL_ODDS_MIN <= odds <= strategy.FOOTBALL_ODDS_MAX
                and not learning.is_blocked(event.get("sport_key"), market, book, overrides)):
            out[book].append({"market": market, "pick": pick, "bet_target": pick, "p_fair": p, "odds": odds,
                              "book": book, "sharp_odds": sharp_odds, "ev": ev})

    h2h = _outcomes_1x2(event)
    if sharp in h2h:
        names = ("HOME_WIN", "DRAW", "AWAY_WIN") if "DRAW" in h2h[sharp] else ("HOME_WIN", "AWAY_WIN")
        fair = devig([h2h[sharp][n] for n in names], "shin" if len(names) == 3 else "power")
        for book, sel in h2h.items():
            if book == sharp or book in EXCHANGES:
                continue
            for n, p in zip(names, fair):
                if n in sel:
                    add(book, "h2h", n, p, sel[n], h2h[sharp][n])
    for pt, books in _outcomes_totals(event).items():
        if sharp not in books:
            continue
        fair = devig([books[sharp]["OVER"], books[sharp]["UNDER"]], "power")
        for book, sel in books.items():
            if book == sharp or book in EXCHANGES:
                continue
            for side, p in zip(("OVER", "UNDER"), fair):
                add(book, "totals", f"{side} {pt:g}", p, sel[side], books[sharp][side])
    return out
