"""Futbol değer seçicisi (Pinnacle'a göre) için çevrimdışı testler: python test_football_value.py"""
import football_value as fv
from market.devig import devig


def book(key, h, d, a, over=None, under=None, point=2.5):
    markets = [{"key": "h2h", "outcomes": [{"name": "Arsenal", "price": h}, {"name": "Chelsea", "price": a},
                                            {"name": "Draw", "price": d}]}]
    if over:
        markets.append({"key": "totals", "outcomes": [{"name": "Over", "price": over, "point": point},
                                                      {"name": "Under", "price": under, "point": point}]})
    return {"key": key, "markets": markets}


def event(*books):
    return {"id": "e1", "sport_key": "soccer_epl", "home_team": "Arsenal", "away_team": "Chelsea",
            "commence_time": "2026-10-25T15:00:00Z", "bookmakers": list(books)}


def test_no_pinnacle_no_reference():
    assert fv.evaluate_event(event(book("unibet_se", 2.0, 3.5, 4.0))) is None


def test_value_found_and_fair_from_pinnacle():
    e = event(book("pinnacle", 2.00, 3.60, 3.90), book("onexbet", 2.20, 3.40, 3.70), book("unibet_se", 2.05, 3.50, 3.80))
    r = fv.evaluate_event(e)
    fair = devig([2.00, 3.60, 3.90], "shin")
    best = r["best"]
    assert best["pick"] == "HOME_WIN" and best["book"] == "onexbet" and best["odds"] == 2.20
    assert abs(best["p_fair"] - fair[0]) < 1e-12 and abs(best["ev"] - (fair[0] * 2.20 - 1)) < 1e-12
    a = fv.to_analysis(e, r)
    assert a["bet_target"] == "HOME_WIN" and a["is_recommended"] and a["odds_value"] == 2.20


def test_exchange_and_sharp_are_not_used_as_price():
    # En yüksek fiyat borsada; borsa (komisyon öncesi) ve Pinnacle'ın kendisi fiyat kaynağı olamaz
    e = event(book("pinnacle", 2.00, 3.60, 3.90), book("betfair_ex_eu", 2.40, 3.70, 4.20), book("unibet_se", 1.95, 3.40, 3.70))
    r = fv.evaluate_event(e)
    assert all(c["book"] not in ("betfair_ex_eu", "pinnacle") for c in r["candidates"])
    assert r["best"] is None  # unibet fiyatları adil fiyatın altında


def test_max_ev_guard_and_odds_range():
    stale = event(book("pinnacle", 2.00, 3.60, 3.90), book("onexbet", 2.60, 3.40, 3.70))  # EV ~ +%27: bayat oran
    assert fv.evaluate_event(stale)["best"] is None
    longshot = event(book("pinnacle", 1.30, 6.00, 11.0), book("onexbet", 1.25, 5.50, 13.0))  # 13.0 > 4.00
    assert fv.evaluate_event(longshot)["best"] is None


def test_totals_matched_by_point():
    e = event(book("pinnacle", 2.0, 3.6, 3.9, over=1.95, under=1.95),
              book("onexbet", 1.9, 3.5, 3.8, over=2.06, under=1.80),
              book("unibet_se", 1.9, 3.5, 3.8, over=2.30, under=1.60, point=3.5))  # farklı çizgi: karşılaştırılmaz
    r = fv.evaluate_event(e)
    over25 = [c for c in r["candidates"] if c["pick"] == "OVER 2.5"][0]
    assert over25["book"] == "onexbet" and abs(over25["p_fair"] - 0.5) < 1e-9
    assert r["best"]["pick"] == "OVER 2.5" and not any(c["pick"] == "OVER 3.5" for c in r["candidates"])
    assert fv.pick_label("OVER 2.5") == "2.5 Üst" and fv.pick_label("DRAW") == "Beraberlik (X)"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
