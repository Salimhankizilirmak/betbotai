"""Faz 2A testleri (çevrimdışı): python test_phase2a.py

DATABASE_URL tanımlıysa ve TEST_DB=1 ise odds_snapshots yazma/okuma da gerçek Postgres'te test edilir.
"""
import asyncio
import math
import os
from datetime import datetime, timedelta, timezone

from market import devig
from market.edge import (expected_value, parlay_odds, parlay_probability, parlay_ev, kelly_fraction,
                         consensus_fair_probability, best_prices)
from backtest import metrics
from ingest import odds_snapshots as snaps


def close(a, b, tol=1e-6):
    return abs(a - b) < tol


# ---------------- de-vig ----------------
def test_devig_sums_to_one_and_removes_margin():
    prices = [1.75, 3.50, 4.60]  # tipik 1X2, ~%7.5 marj
    assert devig.overround(prices) > 0.05
    for method in ("multiplicative", "power", "shin"):
        p = devig.devig(prices, method)
        assert close(sum(p), 1.0), (method, sum(p))
        assert p[0] > p[1] > p[2]
        assert all(pi < 1 / o for pi, o in zip(p, prices))  # marj düşülünce olasılık azalır


def test_devig_fair_market_unchanged():
    prices = [2.0, 2.0]
    for method in ("multiplicative", "power", "shin"):
        assert all(close(x, 0.5) for x in devig.devig(prices, method))


def test_shin_and_power_shift_margin_to_longshot():
    # Sürpriz tarafın olasılığı, multiplicative'e göre daha fazla düşürülmeli (favori-sürpriz yanlılığı)
    prices = [1.25, 6.50, 13.0]
    m = devig.multiplicative(prices)
    for p in (devig.shin(prices), devig.power(prices)):
        assert p[2] < m[2] and p[0] > m[0]


def test_devig_rejects_bad_prices():
    try:
        devig.devig([1.0, 2.0])
        assert False
    except ValueError:
        pass


# ---------------- edge / parlay ----------------
def test_ev_kelly_parlay():
    assert close(expected_value(0.55, 2.0), 0.10)
    assert close(kelly_fraction(0.55, 2.0), 0.10)
    assert kelly_fraction(0.40, 2.0) == 0.0
    odds = [1.6, 1.7, 1.8, 1.65]
    assert close(parlay_odds(odds), 1.6 * 1.7 * 1.8 * 1.65)
    probs = [0.65, 0.62, 0.58, 0.63]
    assert close(parlay_probability(probs), math.prod(probs))
    assert close(parlay_ev(probs, odds), math.prod(probs) * math.prod(odds) - 1)


def test_consensus_prefers_pinnacle_and_best_price():
    rows = [
        {"bookmaker": "pinnacle", "selection": "Over", "price": 1.95},
        {"bookmaker": "pinnacle", "selection": "Under", "price": 1.95},
        {"bookmaker": "unibet", "selection": "Over", "price": 1.80},
        {"bookmaker": "unibet", "selection": "Under", "price": 2.05},
    ]
    fair = consensus_fair_probability(rows)
    assert close(fair["Over"], 0.5) and close(fair["Under"], 0.5)
    assert best_prices(rows) == {"Over": (1.95, "pinnacle"), "Under": (2.05, "unibet")}
    no_sharp = [r for r in rows if r["bookmaker"] != "pinnacle"]
    fair2 = consensus_fair_probability(no_sharp)
    assert close(sum(fair2.values()), 1.0) and fair2["Over"] > fair2["Under"]


# ---------------- metrics ----------------
def test_log_loss_and_brier():
    assert close(metrics.log_loss([0.5, 0.5], [1, 0]), math.log(2))
    assert close(metrics.log_loss([[0.5, 0.3, 0.2]], [0]), -math.log(0.5))
    assert close(metrics.brier([0.8, 0.3], [1, 0]), (0.04 + 0.09) / 2)
    assert close(metrics.brier([[0.5, 0.3, 0.2]], [0]), 0.25 + 0.09 + 0.04)


def test_calibration():
    # Mükemmel kalibrasyon: %20 denen olayların %20'si, %80 denenlerin %80'i gerçekleşiyor
    probs = [0.2] * 10 + [0.8] * 10
    outcomes = [1, 1] + [0] * 8 + [1] * 8 + [0, 0]
    assert close(metrics.expected_calibration_error(probs, outcomes), 0.0)
    over = metrics.expected_calibration_error([0.9] * 10, [1] * 5 + [0] * 5)
    assert close(over, 0.4)


def test_roi_clv_drawdown_streak():
    bets = [{"odds": 2.0, "won": True}, {"odds": 2.0, "won": False}, {"odds": 3.0, "won": False},
            {"odds": 1.5, "won": True, "void": True}]
    assert close(metrics.roi(bets), (1 - 1 - 1) / 3)
    assert close(metrics.clv(2.10, 0.50), 0.05)
    assert close(metrics.clv_price_ratio(2.10, 2.00), 0.05)
    assert close(metrics.max_drawdown([1, -1, -1, -1, 2]), 3)
    assert metrics.longest_losing_streak([True, False, None, False, False, True, False]) == 3


def test_bootstrap_ci_contains_mean():
    vals = [0.1, -0.2, 0.05, 0.3, -0.1, 0.0, 0.15, -0.05] * 10
    lo, hi = metrics.bootstrap_ci(vals)
    mean = sum(vals) / len(vals)
    assert lo < mean < hi


# ---------------- snapshots ----------------
NOW = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)

def sample_match(event_id="ev1", minutes_to_kickoff=10):
    return {
        "id": event_id, "sport_key": "soccer_epl",
        "commence_time": (NOW + timedelta(minutes=minutes_to_kickoff)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home_team": "Arsenal", "away_team": "Chelsea",
        "bookmakers": [{
            "key": "pinnacle", "last_update": "2026-10-23T17:55:00Z",
            "markets": [
                {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 1.9}, {"name": "Chelsea", "price": 4.2},
                                            {"name": "Draw", "price": 3.6}]},
                {"key": "totals", "outcomes": [{"name": "Over", "price": 1.85, "point": 2.5},
                                               {"name": "Under", "price": 2.0, "point": 2.5}]},
                {"key": "spreads", "outcomes": [{"name": "Arsenal", "price": 1.9, "point": -0.5}]},
            ],
        }],
    }

def sample_props():
    return {
        "id": "nba1", "sport_key": "basketball_nba", "commence_time": "2026-10-24T00:00:00Z",
        "home_team": "Los Angeles Lakers", "away_team": "Boston Celtics",
        "bookmakers": [{"key": "draftkings", "markets": [{"key": "player_points", "last_update": "2026-10-23T17:00:00Z",
            "outcomes": [{"name": "Over", "description": "Jayson Tatum", "price": 1.87, "point": 26.5},
                         {"name": "Under", "description": "Jayson Tatum", "price": 1.93, "point": 26.5}]}]}],
    }


def test_flatten_match_and_props():
    rows = snaps.flatten_events([sample_match()])
    assert len(rows) == 6  # 3 h2h + 2 totals + 1 spreads
    t = [r for r in rows if r["market"] == "totals" and r["selection"] == "Over"][0]
    assert t["point"] == 2.5 and t["price"] == 1.85 and t["bookmaker"] == "pinnacle" and t["player"] is None
    assert t["commence_time"] == NOW + timedelta(minutes=10)
    p = snaps.flatten_events(sample_props())  # event-odds yanıtı tek dict
    assert len(p) == 2 and p[0]["player"] == "Jayson Tatum" and p[0]["selection"] == "Over" and p[0]["point"] == 26.5


def test_filter_changed_dedupes_regular_but_keeps_closing():
    snaps._last_price.clear()
    rows = snaps.flatten_events([sample_match()])
    first = snaps.filter_changed(rows)
    assert len(first) == 5  # spreads saklanmaz
    assert snaps.filter_changed(snaps.flatten_events([sample_match()])) == []  # fiyat değişmedi
    changed = sample_match()
    changed["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 1.85
    assert len(snaps.filter_changed(snaps.flatten_events([changed]))) == 1
    assert len(snaps.filter_changed(snaps.flatten_events([sample_match()], kind="closing"))) == 5


def test_events_closing_soon_window():
    events = [sample_match("a", 5), sample_match("b", 14), sample_match("c", 40), sample_match("d", -5)]
    soon = snaps.events_closing_soon(events, now=NOW, window_minutes=15)
    assert [e["id"] for e in soon] == ["a", "b"]


def test_credit_guard():
    import oddsapi_client, strategy
    oddsapi_client.CREDIT_STATE["remaining"] = None
    assert snaps.credits_available()  # henüz bilinmiyorsa izin ver
    oddsapi_client.CREDIT_STATE["remaining"] = strategy.ODDS_CREDIT_RESERVE - 1
    assert not snaps.credits_available()
    oddsapi_client.CREDIT_STATE["remaining"] = strategy.ODDS_CREDIT_RESERVE + 500
    assert snaps.credits_available()
    oddsapi_client.CREDIT_STATE["remaining"] = None


def test_db_roundtrip():
    """Gerçek Postgres: şema + snapshot yazma + kapanış tespiti."""
    if os.getenv("TEST_DB") != "1":
        print("SKIP test_db_roundtrip (TEST_DB=1 ve DATABASE_URL gerekli)")
        return
    import bet_manager
    bet_manager.init_db()
    snaps._last_price.clear()
    ev = sample_match("db_ev1")
    assert asyncio.run(snaps.record_odds_response([ev], kind="regular")) == 5
    assert asyncio.run(snaps.record_odds_response([ev], kind="regular")) == 0  # değişmedi
    assert asyncio.run(snaps.record_odds_response(sample_props(), kind="closing")) == 2
    assert asyncio.run(snaps.record_odds_response([ev], kind="closing")) == 5
    done = snaps._events_with_closing(["db_ev1", "nba1", "yok"])
    assert ("db_ev1", "h2h") in done and ("nba1", "player_points") in done and not any(e == "yok" for e, _ in done)
    with bet_manager.get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM odds_snapshots WHERE event_id IN ('db_ev1', 'nba1')")
        assert cur.fetchone()["n"] == 12
        cur.execute("SELECT player, point, price FROM odds_snapshots WHERE event_id='nba1' AND selection='Over'")
        r = cur.fetchone()
        assert r["player"] == "Jayson Tatum" and abs(r["point"] - 26.5) < 1e-6
        cur.execute("INSERT INTO predictions (model_name, model_version, sport_key, event_id, market, selection, p_model, p_market_fair, edge, meta) "
                    "VALUES ('test', '0', 'soccer_epl', 'db_ev1', 'h2h', 'Arsenal', 0.55, 0.5, 0.05, '{\"k\": 1}'::jsonb) RETURNING id")
        assert cur.fetchone()["id"] > 0
        conn.rollback()
    print("DB roundtrip OK")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
