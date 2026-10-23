"""NBA prop modeli için çevrimdışı testler: python test_nba_props_model.py"""
import numpy as np
import pandas as pd

from models import nba_props_model as m


def synthetic_rows(n_games=30, seed=1):
    """İki takım, iki oyuncu; A oyuncusu ~30 dk / ~0.6 sayı/dk, B oyuncusu ~20 dk / ~0.4 sayı/dk."""
    rng = np.random.default_rng(seed)
    rows = []
    start = pd.Timestamp("2026-01-01")
    for i in range(n_games):
        d = start + pd.Timedelta(days=2 * i)
        gid = f"g{i}"
        for key, team, opp, mins, rate in (("a", "AAA", "BBB", 30, 0.6), ("b", "BBB", "AAA", 20, 0.4)):
            mi = float(rng.normal(mins, 2))
            rows.append({"game_id": gid, "game_date": d, "season": "2025-26", "player_key": key, "player_name": key,
                         "team_abbr": team, "team_name": team, "opponent_abbr": opp, "opponent_name": opp,
                         "is_home": key == "a", "dnp": False, "minutes": mi,
                         "pts": float(rng.poisson(rate * mi)), "reb": float(rng.poisson(0.2 * mi)),
                         "ast": float(rng.poisson(0.1 * mi))})
    return rows


def test_features_do_not_leak_target():
    played = m.prepare(synthetic_rows())
    f1 = m.add_features(played.copy(), m.DEFAULT_PARAMS)
    last = f1[(f1.player_key == "a")].index[-1]
    mu_before = f1.loc[last, "mu_pts"]
    # Hedef satırın kendi sonucunu değiştir: tahmini (mu) değişmemeli
    played2 = played.copy()
    idx = played2[(played2.player_key == "a")].index[-1]
    played2.loc[idx, ["pts", "minutes"]] = [99.0, 48.0]
    f2 = m.add_features(played2, m.DEFAULT_PARAMS)
    mu_after = f2[(f2.player_key == "a")]["mu_pts"].iloc[-1]
    assert abs(mu_before - mu_after) < 1e-9, (mu_before, mu_after)


def test_projection_tracks_player_level():
    # Sentetik veride her takımda tek oyuncu var; rakip faktörü oyuncu seviyesini yansıtacağı için kapatılır
    import copy
    params = copy.deepcopy(m.DEFAULT_PARAMS)
    for s in m.STATS:
        params["rate"][s]["alpha"] = 0.0
    played = m.prepare(synthetic_rows(60))
    f = m.add_features(played, params)
    a = f[(f.player_key == "a") & (f.n_prev >= 20)]
    b = f[(f.player_key == "b") & (f.n_prev >= 20)]
    assert 15 < a["mu_pts"].mean() < 21 and 5 < b["mu_pts"].mean() < 11
    assert 27 < a["min_proj"].mean() < 33


def test_prob_over_under_basic():
    assert abs(m.prob_over(20.0, 19.5, 1e6) + m.prob_under(20.0, 19.5, 1e6) - 1) < 1e-9  # .5 çizgide push yok
    assert m.prob_over(25.0, 19.5, 15) > m.prob_over(20.0, 19.5, 15) > m.prob_over(15.0, 19.5, 15)
    # tam sayı çizgide push olasılığı iki taraftan da düşülür
    assert m.prob_over(20.0, 20.0, 15) + m.prob_under(20.0, 20.0, 15) < 1


def test_negative_binomial_is_calibrated_on_simulated_data():
    rng = np.random.default_rng(7)
    mu, theta = 18.0, 12.0
    x = rng.negative_binomial(theta, theta / (theta + mu), size=20000)
    p = float(m.prob_over(mu, 17.5, theta))
    assert abs(p - (x > 17.5).mean()) < 0.015


def test_live_projection_matches_backtest_features():
    rows = synthetic_rows(30)
    played = m.prepare(rows)
    last_date = played["game_date"].max()
    # Canlı: son maçtan sonraki maç için tahmin; backtest: aynı maçı geçmişe ekleyip özellik hesapla
    model = m.PropModel(rows)
    nxt = last_date + pd.Timedelta(days=2)
    live = model.project("a", nxt, "BBB", season="2025-26")
    future = rows + [{**rows[-2], "game_id": "gx", "game_date": nxt, "minutes": 30.0, "pts": 18.0}]
    f = m.add_features(m.prepare(future), model.params, model.opp_tables)
    bt = f[(f.player_key == "a") & (f.game_id == "gx")].iloc[0]
    assert abs(live["mu_pts"] - bt["mu_pts"]) < 1e-6 and abs(live["min_proj"] - bt["min_proj"]) < 1e-6


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
