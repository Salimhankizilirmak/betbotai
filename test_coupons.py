"""Kupon oluşturucu ve sonuçlandırma için çevrimdışı testler: python test_coupons.py"""
import math
from datetime import datetime, timedelta, timezone

import coupons


def leg(group, odds, p, book="b1", **kw):
    return {"group": group, "odds": odds, "p": p, "ev": p * odds - 1, "book": book,
            "commence_time": kw.pop("ct", "2026-10-24T15:00:00Z"), **kw}


def test_best_coupon_respects_cap_single_book_and_distinct_matches():
    legs = {
        "b1": [leg("m1", 2.0, 0.52), leg("m1", 3.5, 0.30), leg("m2", 2.1, 0.50), leg("m3", 1.9, 0.55), leg("m4", 3.0, 0.35)],
        "b2": [leg("m5", 2.5, 0.42, book="b2")],  # tek bacak: tek başına kupon olamaz
    }
    c = coupons.best_coupon(legs, 2, 5, 8.0)
    assert c["book"] == "b1"
    groups = [l["group"] for l in c["legs"]]
    assert len(groups) == len(set(groups))           # aynı maçtan iki seçim yok
    assert c["odds"] <= 8.0 + 1e-9
    assert abs(c["odds"] - math.prod(l["odds"] for l in c["legs"])) < 1e-9
    assert abs(c["ev"] - (math.prod(l["p"] for l in c["legs"]) * c["odds"] - 1)) < 1e-9
    # tüm geçerli kombinasyonlar içinde EV en yüksek olan
    import itertools
    per = {}
    for l in legs["b1"]:
        if l["group"] not in per or l["ev"] > per[l["group"]]["ev"]:
            per[l["group"]] = l
    best = max((math.prod(x["p"] for x in cb) * math.prod(x["odds"] for x in cb) - 1)
               for k in range(2, 6) for cb in itertools.combinations(per.values(), k)
               if math.prod(x["odds"] for x in cb) <= 8.0)
    assert abs(c["ev"] - best) < 1e-9


def test_no_coupon_when_constraints_impossible():
    assert coupons.best_coupon({"b1": [leg("m1", 2.0, 0.52)]}, 2, 5, 8.0) is None
    assert coupons.best_coupon({"b1": [leg("m1", 3.2, 0.33), leg("m2", 3.0, 0.35)]}, 2, 5, 8.0) is None  # 9.6 > 8


def test_nba_exactly_three_legs_different_games():
    legs = {"dk": [leg(f"g{i}", 1.9, 0.55 + i * 0.005, player=f"p{i}") for i in range(4)] + [leg("g0", 1.9, 0.6, player="x")]}
    c = coupons.best_coupon(legs, 3, 3, None)
    assert len(c["legs"]) == 3 and len({l["group"] for l in c["legs"]}) == 3


def test_coupon_outcome():
    L = lambda r, o=2.0: {"result": r, "odds": o}
    assert coupons.coupon_outcome([L("WON"), L("LOST")]) == ("LOST", -100.0)
    assert coupons.coupon_outcome([L("WON"), L(None)]) == ("PENDING", 0.0)
    assert coupons.coupon_outcome([L("WON", 2.0), L("WON", 1.5)]) == ("WON", 200.0)
    assert coupons.coupon_outcome([L("WON", 2.0), L("VOID", 1.5)]) == ("WON", 100.0)  # iade bacak oran 1
    assert coupons.coupon_outcome([L("VOID"), L("VOID")]) == ("VOID", 0.0)


def test_leg_result_football():
    scores = {"soccer_epl": [{"id": "e1", "completed": True, "home_team": "A", "away_team": "B",
                              "scores": [{"name": "A", "score": "2"}, {"name": "B", "score": "1"}]},
                             {"id": "e2", "completed": False, "home_team": "C", "away_team": "D", "scores": None}]}
    L = lambda pick, eid="e1": {"sport_key": "soccer_epl", "event_id": eid, "pick": pick}
    assert coupons.leg_result_football(L("HOME_WIN"), scores) == "WON"
    assert coupons.leg_result_football(L("DRAW"), scores) == "LOST"
    assert coupons.leg_result_football(L("OVER 2.5"), scores) == "WON"
    assert coupons.leg_result_football(L("UNDER 2.5"), scores) == "LOST"
    assert coupons.leg_result_football(L("OVER 3"), scores) == "VOID"
    assert coupons.leg_result_football(L("HOME_WIN", "e2"), scores) is None


def test_football_legs_window_and_message():
    now = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)
    def ev(eid, hours, price):
        ct = (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bk = lambda k, h: {"key": k, "markets": [{"key": "h2h", "outcomes": [
            {"name": "A" + eid, "price": h}, {"name": "B" + eid, "price": 3.9}, {"name": "Draw", "price": 3.6}]}]}
        return {"id": eid, "sport_key": "soccer_epl", "home_team": "A" + eid, "away_team": "B" + eid,
                "commence_time": ct, "bookmakers": [bk("pinnacle", 2.0), bk("onexbet", price)]}
    events = [ev("1", 3, 2.15), ev("2", 5, 2.15), ev("3", 30, 2.15), ev("4", 0.2, 2.15)]  # 3: 24 saat dışı, 4: çok yakın
    legs = coupons.football_legs(events, now=now)
    assert {l["group"] for l in legs["onexbet"]} == {"1", "2"}
    c = coupons.best_coupon(legs, 2, 5, 8.0)
    text = coupons.message("football", c)
    assert "GÜNÜN FUTBOL KUPONU" in text and "onexbet" in text and "Toplam oran" in text and "İddaa" in text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
