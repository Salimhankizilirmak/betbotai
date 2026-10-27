"""Öğrenme döngüsü (learning.py) için çevrimdışı testler: python test_learning.py"""
import math
import time

import learning
from market.devig import devig

learning._overrides_cache.update(v={}, ts=time.time())  # testler DB'ye gitmesin


def test_close_fair_h2h_and_totals():
    close = {"home": "A", "away": "B", "h2h": {"A": 2.0, "Draw": 3.6, "B": 3.9},
             "totals": {2.5: {"Over": 1.95, "Under": 1.95}}}
    f = devig([2.0, 3.6, 3.9], "shin")
    assert abs(learning.close_fair("HOME_WIN", close) - f[0]) < 1e-12
    assert abs(learning.close_fair("DRAW", close) - f[1]) < 1e-12
    assert abs(learning.close_fair("OVER 2.5", close) - 0.5) < 1e-12
    assert learning.close_fair("OVER 3.5", close) is None
    nba = {"home": "A", "away": "B", "h2h": {"A": 1.8, "B": 2.1}, "totals": {}}
    assert abs(learning.close_fair("AWAY_WIN", nba) - devig([1.8, 2.1], "power")[1]) < 1e-12
    assert learning.close_fair("HOME_WIN", None) is None


def test_clv_and_bet_stats():
    st = learning.clv_stats([{"clv": 0.05}, {"clv": -0.01}, {"clv": 0.02}, {"clv": None}])
    assert st["n"] == 3 and abs(st["avg"] - 0.02) < 1e-12 and abs(st["positive_share"] - 2 / 3) < 1e-12
    bets = [{"status": "WON", "bet_amount": 10, "profit": 10, "p_fair": 0.5},
            {"status": "LOST", "bet_amount": 10, "profit": -10, "p_fair": 0.5},
            {"status": "LOST", "bet_amount": 10, "profit": -10, "p_fair": 0.5},
            {"status": "PENDING", "bet_amount": 10, "profit": 0, "p_fair": 0.5}]
    s = learning.bet_stats(bets)
    assert s["n"] == 3 and s["wins"] == 1 and abs(s["expected_hit"] - 0.5) < 1e-12
    assert abs(s["z"] - (1 - 1.5) / math.sqrt(0.75)) < 1e-12 and abs(s["roi"] - (-10 / 30)) < 1e-12


def _report(seg_stats, band):
    return {"picks_clv_by": {"league": seg_stats, "market": {}, "bookmaker": {}}, "ev_band_2_3": band}


def test_decide_overrides_needs_enough_samples_and_confident_negative():
    neg = {"n": 80, "avg": -0.03, "ci90": [-0.05, -0.01]}
    small = {"n": 20, "avg": -0.10, "ci90": [-0.2, -0.02]}
    unsure = {"n": 120, "avg": -0.01, "ci90": [-0.03, 0.01]}
    o, ch = learning.decide_overrides(_report({"epl": neg, "turkey": small, "serie_a": unsure}, {"n": 0}), {})
    assert o["blocked"]["league"] == ["epl"] and len(ch) == 1
    # kapalı segment CLV'si pozitife dönünce açılır
    o2, ch2 = learning.decide_overrides(_report({"epl": {"n": 90, "avg": 0.01, "ci90": [-0.01, 0.03]}}, {"n": 0}), o)
    assert o2["blocked"]["league"] == [] and "tekrar açıldı" in ch2[0]


def test_min_ev_band_and_bounds():
    o, ch = learning.decide_overrides(_report({}, {"n": 150, "avg": -0.02, "ci90": [-0.03, -0.005]}), {})
    assert o["min_ev"] == 0.03 and ch
    o2, _ = learning.decide_overrides(_report({}, {"n": 150, "avg": 0.004, "ci90": [-0.01, 0.02]}), o)
    assert o2["min_ev"] == 0.02
    o3, ch3 = learning.decide_overrides(_report({}, {"n": 40, "avg": -0.05, "ci90": [-0.08, -0.02]}), {})
    assert o3["min_ev"] == 0.02 and not ch3  # yetersiz örnek: değişiklik yok
    assert learning.min_ev({"min_ev": 0.2}) == 0.05 and learning.min_ev({"min_ev": 0.0}) == 0.02


def test_is_blocked_and_odds_bucket():
    o = {"blocked": {"league": ["epl"], "market": ["totals"], "bookmaker": ["onexbet"]}}
    assert learning.is_blocked("soccer_epl", "h2h", "unibet_se", o)
    assert learning.is_blocked("soccer_spain_la_liga", "totals", "unibet_se", o)
    assert learning.is_blocked("soccer_spain_la_liga", "h2h", "onexbet", o)
    assert not learning.is_blocked("soccer_spain_la_liga", "h2h", "unibet_se", o)
    assert learning.odds_bucket(1.45) == "1.30-1.60" and learning.odds_bucket(3.9) == "3.20-4.00"
    assert learning.odds_bucket(5.5) == ">4.00" and learning.odds_bucket(1.2) == "<1.30"


def test_report_message_renders():
    r = {"days": 90, "picks_clv": {"n": 0}, "picks_clv_by": {}, "bets_by_method": {}, "calibration": [], "coupons": []}
    text = learning.report_message(r, [], {"blocked": {}, "min_ev": 0.02})
    assert "Haftalık öğrenme raporu" in text and "EV eşiği %2" in text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
