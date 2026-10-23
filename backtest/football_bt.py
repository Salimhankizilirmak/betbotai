"""
Futbol Dixon-Coles backtest'i (walk-forward, football-data.co.uk maç + oranları).

    python -m backtest.football_bt            # 2022-23'te ayarla, 2023-24..bugün test et, raporla, kaydet
    python -m backtest.football_bt --no-fit   # kayıtlı parametrelerle sadece test

- Bahis fiyatı: Bet365 AÇILIŞ oranı (İddaa'ya en yakın, bahis anında alınabilecek fiyat)
- Keskin kapanış (CLV referansı): Pinnacle kapanış > Betfair Borsa kapanış > piyasa ortalaması kapanış
- Olasılık: model ile piyasanın (Bet365 açılış, marjsız) ağırlıklı ortalaması; ağırlık ayar sezonunda seçilir
"""
import argparse
import itertools
import json
import logging
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import football_data
from backtest import metrics
from market.devig import devig
from models import football_dc as fdc

VALID_SEASONS = ["2223"]
TEST_SEASONS = ["2324", "2425", "2526", "2627"]
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
EDGE_THRESHOLDS = (0.02, 0.03, 0.05, 0.08)
COUPON_MAX_ODDS, COUPON_MAX_LEGS, COUPON_MIN_LEGS = 8.0, 5, 2

def _odds(o, keys):
    vals = [o.get(k) for k in keys]
    return vals if all(v and v > 1.0 for v in vals) else None

def market_columns(o):
    """(1X2 açılış B365, OU2.5 açılış B365, 1X2 keskin kapanış, OU2.5 keskin kapanış) — yoksa None."""
    open_1x2 = _odds(o, ["B365H", "B365D", "B365A"])
    open_ou = _odds(o, ["B365>2.5", "B365<2.5"])
    close_1x2 = _odds(o, ["PSCH", "PSCD", "PSCA"]) or _odds(o, ["BFECH", "BFECD", "BFECA"]) or _odds(o, ["AvgCH", "AvgCD", "AvgCA"])
    close_ou = _odds(o, ["PC>2.5", "PC<2.5"]) or _odds(o, ["BFEC>2.5", "BFEC<2.5"]) or _odds(o, ["AvgC>2.5", "AvgC<2.5"])
    return open_1x2, open_ou, close_1x2, close_ou

def walk_forward(df, seasons, xi, kappa, step_days=7, window_days=3 * 365):
    """Her lig için step_days'te bir yeniden fit; sadece fit tarihinden önceki maçlar kullanılır."""
    out = []
    for league, lg in df.groupby("league"):
        target = lg[lg["season"].isin(seasons)]
        if target.empty:
            continue
        start, end = target["match_date"].min(), target["match_date"].max()
        warm = None
        d = start
        while d <= end:
            chunk = target[(target["match_date"] >= d) & (target["match_date"] < d + pd.Timedelta(days=step_days))]
            if not chunk.empty:
                try:
                    m = fdc.DixonColes(xi, kappa).fit(lg, d, window_days, warm_start=warm)
                    warm = (m.teams, m.theta)
                except ValueError:
                    d += pd.Timedelta(days=step_days)
                    continue
                for r in chunk.itertuples(index=False):
                    p = fdc.market_probs(m.score_matrix(r.home_team, r.away_team))
                    out.append({"match_key": r.match_key, "league": league, "season": r.season,
                                "match_date": r.match_date, "home_team": r.home_team, "away_team": r.away_team,
                                "fthg": r.fthg, "ftag": r.ftag, "odds": r.odds,
                                "known": m.known(r.home_team) and m.known(r.away_team), **{f"m_{k}": v for k, v in p.items()}})
            d += pd.Timedelta(days=step_days)
    return pd.DataFrame(out)

def attach_market(pred):
    rows = []
    for r in pred.to_dict("records"):
        o1, oou, c1, cou = market_columns(r["odds"] or {})
        if not o1:
            continue
        r["open_1x2"], r["open_ou"] = o1, oou
        r["p_open"] = devig(o1, "shin")
        r["p_open_ou"] = devig(oou, "power") if oou else None
        r["p_close"] = devig(c1, "shin") if c1 else None
        r["p_close_ou"] = devig(cou, "power") if cou else None
        r["y_1x2"] = 0 if r["fthg"] > r["ftag"] else (1 if r["fthg"] == r["ftag"] else 2)
        r["y_over"] = int(r["fthg"] + r["ftag"] > 2.5)
        rows.append(r)
    return pd.DataFrame(rows)

def model_1x2(r):
    return [r["m_home"], r["m_draw"], r["m_away"]]

def model_ou(r):
    return [r["m_over_2.5"], r["m_under_2.5"]]

def logloss_1x2(df, probs_fn):
    return metrics.log_loss([list(probs_fn(r)) for r in df.to_dict("records")], list(df["y_1x2"]))

def logloss_ou(df, probs_fn):
    d = df[df["p_open_ou"].notna()]
    return metrics.log_loss([float(probs_fn(r)[0]) for r in d.to_dict("records")], list(d["y_over"]))

def fit_blend(valid):
    grid = np.round(np.arange(0, 1.0001, 0.05), 2)
    best = {}
    ll = [logloss_1x2(valid, lambda r, w=w: fdc.blend(model_1x2(r), r["p_open"], w)) for w in grid]
    best["1x2"] = float(grid[int(np.argmin(ll))])
    v = valid[valid["p_open_ou"].notna()]
    ll = [logloss_ou(v, lambda r, w=w: fdc.blend(model_ou(r), r["p_open_ou"], w)) for w in grid]
    best["ou25"] = float(grid[int(np.argmin(ll))])
    return best

def fit(df):
    t0 = time.time()
    results = []
    for xi, kappa in itertools.product([0.001, 0.0019, 0.0035], [0.5, 2.0, 5.0]):
        pred = attach_market(walk_forward(df, VALID_SEASONS, xi, kappa, step_days=14))
        results.append((logloss_1x2(pred, model_1x2), xi, kappa))
        logging.info(f"xi={xi} kappa={kappa}: ayar sezonu 1X2 log loss {results[-1][0]:.4f}")
    _, xi, kappa = min(results)
    valid = attach_market(walk_forward(df, VALID_SEASONS, xi, kappa, step_days=14))
    w = fit_blend(valid)
    logging.info(f"seçilen xi={xi} kappa={kappa}, karışım ağırlığı (model payı) {w} [{time.time() - t0:.0f} sn]")
    return {"version": f"football-dc-v1-{datetime.now(timezone.utc):%Y%m%d}", "xi": xi, "kappa": kappa,
            "window_days": 3 * 365, "blend_w": w, "fitted_on": {"valid_seasons": VALID_SEASONS}}

# ---------------------------------------------------------------- bahis simülasyonu
def selections(r, w):
    """Bir maçın aday seçimleri: (market, outcome, p_blend, p_open_fair, odds_open, p_close_fair, won)."""
    out = []
    pb = fdc.blend(model_1x2(r), r["p_open"], w["1x2"])
    for i, name in enumerate(("HOME_WIN", "DRAW", "AWAY_WIN")):
        out.append({"market": "1x2", "pick": name, "p": pb[i], "fair": r["p_open"][i], "odds": r["open_1x2"][i],
                    "close": r["p_close"][i] if r["p_close"] else None, "won": r["y_1x2"] == i})
    if r["p_open_ou"]:
        po = fdc.blend(model_ou(r), r["p_open_ou"], w["ou25"])
        for i, name in enumerate(("OVER 2.5", "UNDER 2.5")):
            out.append({"market": "ou25", "pick": name, "p": po[i], "fair": r["p_open_ou"][i], "odds": r["open_ou"][i],
                        "close": r["p_close_ou"][i] if r["p_close_ou"] else None,
                        "won": (r["y_over"] == 1) if i == 0 else (r["y_over"] == 0)})
    for s in out:
        s["edge"], s["ev"] = s["p"] - s["fair"], s["p"] * s["odds"] - 1
        s["clv"] = s["odds"] * s["close"] - 1 if s["close"] else None
    return out

def summarize_bets(bets):
    if not bets:
        return {"n": 0}
    profits = [metrics.bet_profit(b["odds"], b["won"]) for b in bets]
    clvs = [b["clv"] for b in bets if b["clv"] is not None]
    lo, hi = metrics.bootstrap_ci(profits)
    out = {"n": len(bets), "hit_rate": float(np.mean([b["won"] for b in bets])),
           "avg_odds": float(np.mean([b["odds"] for b in bets])), "roi": float(np.mean(profits)),
           "roi_ci90": [float(lo), float(hi)], "max_drawdown_units": metrics.max_drawdown(profits),
           "longest_losing_streak": metrics.longest_losing_streak([b["won"] for b in bets])}
    if clvs:
        clo, chi = metrics.bootstrap_ci(clvs)
        out.update(avg_clv=float(np.mean(clvs)), clv_ci90=[float(clo), float(chi)], clv_positive_share=float(np.mean(np.array(clvs) > 0)))
    return out

def bet_sim(test, w, threshold, by_league=False):
    bets = []
    for r in test.to_dict("records"):
        cand = [s for s in selections(r, w) if s["edge"] >= threshold and s["ev"] > 0 and 1.3 <= s["odds"] <= 4.0]
        if cand:
            b = max(cand, key=lambda s: s["ev"])
            bets.append({**b, "league": r["league"], "date": r["match_date"]})
    res = summarize_bets(bets)
    if by_league:
        res["by_league"] = {lg: summarize_bets([b for b in bets if b["league"] == lg]) for lg in sorted({b["league"] for b in bets})}
    return res, bets

def coupon_sim(bets):
    """Her gün bir kupon: edge'li tekli adaylardan EV sırasıyla, farklı maçlar, toplam oran <= 8.00, 2-5 maç."""
    by_day = {}
    for b in bets:
        by_day.setdefault(pd.Timestamp(b["date"]).date(), []).append(b)
    coupons = []
    for day, cands in sorted(by_day.items()):
        legs, odds = [], 1.0
        for b in sorted(cands, key=lambda s: -s["ev"]):
            if len(legs) >= COUPON_MAX_LEGS or odds * b["odds"] > COUPON_MAX_ODDS:
                continue
            legs.append(b)
            odds *= b["odds"]
        if len(legs) >= COUPON_MIN_LEGS:
            coupons.append({"date": day, "legs": len(legs), "odds": odds, "won": all(l["won"] for l in legs),
                            "p_model": float(np.prod([l["p"] for l in legs]))})
    if not coupons:
        return {"n": 0}
    profits = [metrics.bet_profit(c["odds"], c["won"]) for c in coupons]
    lo, hi = metrics.bootstrap_ci(profits)
    return {"n_coupons": len(coupons), "hit_rate": float(np.mean([c["won"] for c in coupons])),
            "model_expected_hit_rate": float(np.mean([c["p_model"] for c in coupons])),
            "avg_odds": float(np.mean([c["odds"] for c in coupons])), "avg_legs": float(np.mean([c["legs"] for c in coupons])),
            "roi": float(np.mean(profits)), "roi_ci90": [float(lo), float(hi)],
            "longest_losing_streak": metrics.longest_losing_streak([c["won"] for c in coupons]),
            "max_drawdown_units": metrics.max_drawdown(profits)}

def value_sim(df, seasons=("2324", "2425", "2526"), price_prefix="Max", thresholds=(0.0, 0.02, 0.04)):
    """
    Model yok: olasılık = Pinnacle AÇILIŞ (marjsız). Fiyat = price_prefix açılış (Max = şirketler arası en iyi,
    B365 = tek şirket). EV >= eşik olan en iyi seçime (maç başına bir) sabit stake. CLV: Pinnacle kapanış.
    """
    p1 = [f"{price_prefix}{s}" for s in "HDA"]
    pou = [f"{price_prefix}>2.5", f"{price_prefix}<2.5"]
    out = {}
    for thr in thresholds:
        bets = []
        for r in df[df["season"].isin(seasons)].itertuples():
            o = r.odds or {}
            y = 0 if r.fthg > r.ftag else (1 if r.fthg == r.ftag else 2)
            yo = int(r.fthg + r.ftag > 2.5)
            cands = []
            for fair_k, price_k, close_k, method, won_fn in (
                    (["PSH", "PSD", "PSA"], p1, ["PSCH", "PSCD", "PSCA"], "shin", lambda i: y == i),
                    (["P>2.5", "P<2.5"], pou, ["PC>2.5", "PC<2.5"], "power", lambda i: (yo == 1) if i == 0 else (yo == 0))):
                fair, price, close = _odds(o, fair_k), _odds(o, price_k), _odds(o, close_k)
                if not (fair and price):
                    continue
                f = devig(fair, method)
                c = devig(close, method) if close else None
                for i in range(len(f)):
                    cands.append({"ev": f[i] * price[i] - 1, "odds": price[i], "won": won_fn(i),
                                  "clv": price[i] * c[i] - 1 if c else None, "date": r.match_date})
            cands = [c for c in cands if c["ev"] >= thr and 1.3 <= c["odds"] <= 4.0]
            if cands:
                bets.append(max(cands, key=lambda c: c["ev"]))
        res = summarize_bets(bets)
        res["coupon_8x"] = coupon_sim([{**b, "p": 1 / b["odds"] * (1 + b["ev"])} for b in bets])
        out[f"EV>={thr:.2f}"] = res
    return out

def evaluate(df, params):
    pred = attach_market(walk_forward(df, TEST_SEASONS, params["xi"], params["kappa"], step_days=7,
                                      window_days=params.get("window_days", 3 * 365)))
    w = params["blend_w"]
    closed = pred[pred["p_close"].notna()]
    ou = pred[pred["p_open_ou"].notna()]
    out = {"test_seasons": TEST_SEASONS, "matches": int(len(pred)),
           "log_loss_1x2": {
               "model": logloss_1x2(pred, model_1x2),
               "bet365_acilis": logloss_1x2(pred, lambda r: r["p_open"]),
               "model_piyasa_karisimi": logloss_1x2(pred, lambda r: fdc.blend(model_1x2(r), r["p_open"], w["1x2"])),
               "keskin_kapanis": logloss_1x2(closed, lambda r: r["p_close"]),
           },
           "log_loss_ou25": {
               "model": logloss_ou(ou, model_ou),
               "bet365_acilis": logloss_ou(ou, lambda r: r["p_open_ou"]),
               "model_piyasa_karisimi": logloss_ou(ou, lambda r: fdc.blend(model_ou(r), r["p_open_ou"], w["ou25"])),
           },
           "ece_model_home": metrics.expected_calibration_error(list(pred["m_home"]), list((pred["y_1x2"] == 0).astype(int))),
           "ece_model_over25": metrics.expected_calibration_error(list(ou["m_over_2.5"]), list(ou["y_over"])),
           "bets": {}}
    for t in EDGE_THRESHOLDS:
        res, bets = bet_sim(pred, w, t, by_league=(t == 0.03))
        res["coupon_8x"] = coupon_sim(bets)
        out["bets"][f"edge>={t:.2f}"] = res
    pure = {"1x2": 1.0, "ou25": 1.0}
    out["bets_model_only_edge>=0.05"], _ = bet_sim(pred, pure, 0.05)
    # Modelsiz karşılaştırma: keskin piyasaya (Pinnacle) göre değer — en iyi oran ve tek şirket (Bet365)
    out["value_vs_pinnacle"] = {"best_price_max": value_sim(df, price_prefix="Max"),
                                "single_book_b365": value_sim(df, price_prefix="B365")}
    return out, pred

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fit", action="store_true")
    args = ap.parse_args()
    df = football_data.load_matches()
    df["match_date"] = pd.to_datetime(df["match_date"])
    logging.info(f"maç: {len(df)}, lig: {df['league'].nunique()}, {df['match_date'].min().date()}..{df['match_date'].max().date()}")
    params = fdc.load_params() if args.no_fit else fit(df)
    if not args.no_fit:
        fdc.save_params(params)
    t0 = time.time()
    ev, _ = evaluate(df, params)
    logging.info(f"test bitti [{time.time() - t0:.0f} sn]")
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, "football_backtest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "params": params, "evaluation": ev},
                  f, indent=2, ensure_ascii=False, default=str)
    logging.info(f"rapor: {path}")

if __name__ == "__main__":
    main()
