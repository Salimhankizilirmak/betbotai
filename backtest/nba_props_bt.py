"""
NBA prop modeli backtest'i (walk-forward, ESPN box score'ları üzerinde).

    python -m backtest.nba_props_bt            # parametreleri eğitimde seç, testte raporla, kaydet
    python -m backtest.nba_props_bt --no-fit   # kayıtlı parametrelerle sadece raporla

Geçmiş prop çizgisi verisi olmadığı için:
  1) projeksiyon doğruluğu (MAE) basit yöntemlerle karşılaştırılır,
  2) kalibrasyon ve eski 'patlama' kuralı "yapay çizgiler" ile ölçülür
     (çizgi ≈ bahis şirketi tahmini: (sezon ort. + son 5 ort.) / 2, .5'e yuvarlanmış),
  3) odds_snapshots'ta biriken GERÇEK prop çizgileri varsa onlarla edge/ROI ölçülür.
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

from backtest import metrics
from models import nba_props_model as m

TRAIN_END = pd.Timestamp("2026-02-15")
MIN_PREV_GAMES = 5
MIN_PROJ_MINUTES = 15.0
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")

def load_rows():
    from bet_manager import get_db_connection
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM nba_player_games ORDER BY game_date")
        return [dict(r) for r in cur.fetchall()]

def baselines(df):
    """Maç öncesi basit tahminler: sezon ortalaması, son 5, son 3 (aynı satırlar için)."""
    g = df.groupby("player_key", sort=False)
    gs = df.groupby(["player_key", "season"], sort=False)
    for s in m.STATS:
        df[f"season_avg_{s}"] = gs[s].transform(lambda x: x.shift().expanding().mean())
        df[f"last5_{s}"] = g[s].transform(lambda x: x.shift().rolling(5, min_periods=1).mean())
        df[f"last3_{s}"] = g[s].transform(lambda x: x.shift().rolling(3, min_periods=1).mean())
    return df

def eval_mask(df):
    return (df["n_prev"] >= MIN_PREV_GAMES) & (df["min_proj"] >= MIN_PROJ_MINUTES) & df["minutes"].notna()

def mae(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = ~(np.isnan(a) | np.isnan(b))
    return float(np.mean(np.abs(a[ok] - b[ok]))) if ok.any() else float("nan")

# ---------------------------------------------------------------- fit
def fit(played, base_params):
    params = json.loads(json.dumps(base_params))
    opp = m.opponent_allowed(played)

    t0 = time.time()
    best = None
    for hl, gamma, b2b in itertools.product([3.0, 5.0, 10.0], [0.5, 0.75, 1.0], [0.95, 1.0]):
        params.update(min_halflife=hl, min_gamma=gamma, b2b_factor=b2b)
        f = m.add_features(played.copy(), params, opp)
        tr = f[eval_mask(f) & (f["game_date"] < TRAIN_END)]
        score = mae(tr["min_proj"], tr["minutes"])
        if best is None or score < best[0]:
            best = (score, hl, gamma, b2b)
    _, hl, gamma, b2b = best
    params.update(min_halflife=hl, min_gamma=gamma, b2b_factor=b2b)
    logging.info(f"dakika parametreleri: halflife={hl} gamma={gamma} b2b={b2b} (eğitim MAE {best[0]:.2f}) "
                 f"[{time.time() - t0:.0f} sn]")

    t0 = time.time()
    scores = {s: [] for s in m.STATS}
    for hl_r, beta, alpha in itertools.product([5.0, 10.0, 20.0], [0.3, 0.5, 0.7], [0.0, 0.5, 1.0]):
        for s in m.STATS:
            params["rate"][s] = {"halflife": hl_r, "beta": beta, "alpha": alpha}
        f = m.add_features(played.copy(), params, opp)
        tr = f[eval_mask(f) & (f["game_date"] < TRAIN_END)]
        for s in m.STATS:
            scores[s].append((mae(tr[f"mu_{s}"], tr[s]), hl_r, beta, alpha))
    for s in m.STATS:
        sc, hl_r, beta, alpha = min(scores[s])
        params["rate"][s] = {"halflife": hl_r, "beta": beta, "alpha": alpha}
        logging.info(f"{s}: halflife={hl_r} beta={beta} alpha={alpha} (eğitim MAE {sc:.3f})")
    logging.info(f"oran parametreleri [{time.time() - t0:.0f} sn]")

    # θ (dağılım genişliği): kullanım amacı üst/alt olasılığı olduğu için, eğitimdeki (yapay) çizgilerde
    # ikili log loss'u en aza indiren θ seçilir. Tüm sayıların olabilirliğine göre seçilen θ (sayıda ~4.6)
    # dağılımı fazla sağa çarpık yapıp ortalama civarındaki çizgilerde P(üst)'ü sistematik düşürüyordu.
    f = baselines(m.add_features(played.copy(), params, opp))
    tr = f[eval_mask(f) & (f["game_date"] < TRAIN_END)]
    grid = np.exp(np.linspace(np.log(2.0), np.log(400.0), 60))
    for s in m.STATS:
        line = synthetic_line(tr[f"season_avg_{s}"].fillna(tr[f"last5_{s}"]), tr[f"last5_{s}"])
        ok = line.notna() & tr[f"mu_{s}"].notna()
        y = (tr.loc[ok, s] > line[ok]).astype(int).values
        losses = [metrics.log_loss(list(m.prob_over(tr.loc[ok, f"mu_{s}"], line[ok], th)), list(y)) for th in grid]
        params["theta"][s] = float(grid[int(np.argmin(losses))])
        logging.info(f"{s}: theta={params['theta'][s]:.1f} (eğitim ikili log loss {min(losses):.4f})")
    params["version"] = f"nba-props-v1-{datetime.now(timezone.utc):%Y%m%d}"
    params["fitted_on"] = {"train_end": str(TRAIN_END.date()), "rows": int(len(tr))}
    return params

# ---------------------------------------------------------------- değerlendirme
def synthetic_line(season_avg, last5):
    base = 0.5 * season_avg + 0.5 * last5
    return np.floor(base) + 0.5

def evaluate(played, params):
    f = m.add_features(played.copy(), params, m.opponent_allowed(played))
    f = baselines(f)
    test = f[eval_mask(f) & (f["game_date"] >= TRAIN_END)].copy()
    out = {"test_rows": int(len(test)), "test_from": str(TRAIN_END.date()),
           "test_to": str(test["game_date"].max().date()) if len(test) else None, "stats": {}}
    g = f.groupby("player_key", sort=False)
    for s in m.STATS:
        r = {}
        r["mae"] = {
            "model": mae(test[f"mu_{s}"], test[s]),
            "sezon_ortalamasi": mae(test[f"season_avg_{s}"].fillna(test[f"last5_{s}"]), test[s]),
            "son_5_mac": mae(test[f"last5_{s}"], test[s]),
            "son_3_mac": mae(test[f"last3_{s}"], test[s]),
        }
        # yapay çizgi
        line = synthetic_line(test[f"season_avg_{s}"].fillna(test[f"last5_{s}"]), test[f"last5_{s}"])
        ok = line.notna() & test[f"mu_{s}"].notna()
        t, line = test[ok], line[ok]
        theta = params["theta"][s]
        p_over = m.prob_over(t[f"mu_{s}"], line, theta)
        y_over = (t[s] > line).astype(int).values
        r["synthetic_lines"] = {
            "n": int(len(t)),
            "gercek_ust_orani": float(y_over.mean()),
            "log_loss_model": metrics.log_loss(list(p_over), list(y_over)),
            "log_loss_yazi_tura_0.5": metrics.log_loss([0.5] * len(y_over), list(y_over)),
            "brier_model": metrics.brier(list(p_over), list(y_over)),
            "ece_model": metrics.expected_calibration_error(list(p_over), list(y_over)),
            "calibration": metrics.calibration_table(list(p_over), list(y_over)),
        }
        # model seçimleri: olasılığı >= 0.58 olan taraf (yapay çizgiye karşı)
        picks = []
        for p, y in zip(p_over, y_over):
            if p >= 0.58:
                picks.append(y == 1)
            elif p <= 0.42:
                picks.append(y == 0)
        r["model_picks_p058"] = {"n": len(picks), "hit_rate": float(np.mean(picks)) if picks else None}

        # eski 'patlama' kuralı: son 3 maçın en az 2'sinde çizginin altında ve ort. çizginin altında -> ÜST
        s1, s2, s3 = (g[s].shift(k).loc[t.index] for k in (1, 2, 3))
        below = (s1 < line).astype(int) + (s2 < line).astype(int) + (s3 < line).astype(int)
        old = s3.notna() & (below >= 2) & ((s1 + s2 + s3) / 3 < line)
        old_hits = (t.loc[old, s] > line[old]).values
        r["eski_patlama_kurali"] = {"n": int(old.sum()), "hit_rate": float(old_hits.mean()) if len(old_hits) else None}
        out["stats"][s] = r
    out["minutes_mae"] = {"model": mae(test["min_proj"], test["minutes"]),
                          "son_5_mac": mae(g["minutes"].transform(lambda x: x.shift().rolling(5, min_periods=1).mean())
                                           .loc[test.index], test["minutes"])}
    return out, f

def real_line_eval(features, params):
    """odds_snapshots'taki gerçek prop çizgileri (son snapshot) + sonuçlar -> edge eşiğine göre isabet/ROI."""
    from bet_manager import get_db_connection
    import espn_nba
    from market.devig import devig
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (event_id, bookmaker, market, player, point, selection)
                   event_id, commence_time, bookmaker, market, player, point, selection, price, snapshot_kind
            FROM odds_snapshots
            WHERE market IN ('player_points', 'player_rebounds', 'player_assists') AND player IS NOT NULL
            ORDER BY event_id, bookmaker, market, player, point, selection,
                     (snapshot_kind = 'closing') DESC, captured_at DESC
        """)
        snaps = pd.DataFrame([dict(r) for r in cur.fetchall()])
    if snaps.empty:
        return {"n_lines": 0, "note": "Henüz gerçek prop çizgisi yok"}
    snaps["stat"] = snaps["market"].map({"player_points": "pts", "player_rebounds": "reb", "player_assists": "ast"})
    snaps["player_key"] = snaps["player"].map(espn_nba.player_key)
    snaps["game_date"] = pd.to_datetime(snaps["commence_time"].map(lambda t: espn_nba.us_game_date(t.isoformat())))
    wide = snaps.pivot_table(index=["event_id", "bookmaker", "stat", "player_key", "point", "game_date"],
                             columns="selection", values="price", aggfunc="first").reset_index()
    wide = wide.dropna(subset=["Over", "Under"])
    feats = features[["player_key", "game_date"] + [f"mu_{s}" for s in m.STATS] + list(m.STATS)]
    j = wide.merge(feats, on=["player_key", "game_date"], how="inner")
    j = j[j.apply(lambda r: pd.notna(r[r["stat"]]), axis=1)]
    if j.empty:
        return {"n_lines": int(len(wide)), "n_resolved": 0, "note": "Çizgiler var ama sonuçlanmış maç yok"}
    rows = []
    for _, r in j.iterrows():
        s = r["stat"]
        p_over = float(m.prob_over(r[f"mu_{s}"], r["point"], params["theta"][s]))
        p_under = float(m.prob_under(r[f"mu_{s}"], r["point"], params["theta"][s]))
        fair_over, fair_under = devig([r["Over"], r["Under"]], "power")
        for side, p, fair, price, won in (("over", p_over, fair_over, r["Over"], r[s] > r["point"]),
                                          ("under", p_under, fair_under, r["Under"], r[s] < r["point"])):
            rows.append({"edge": p - fair, "ev": p * price - 1, "odds": price, "won": bool(won),
                         "void": bool(r[s] == r["point"])})
    res = pd.DataFrame(rows)
    out = {"n_lines": int(len(wide)), "n_resolved": int(len(j))}
    for t in (0.0, 0.03, 0.05, 0.08):
        sel = res[res["edge"] >= t]
        out[f"edge>={t:.2f}"] = {"n": int(len(sel)),
                                 "hit_rate": float(sel["won"].mean()) if len(sel) else None,
                                 "roi": metrics.roi(sel.to_dict("records")) if len(sel) else None}
    return out

def write_report(fit_params, ev, real):
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, "nba_props_backtest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "params": fit_params,
                   "evaluation": ev, "real_lines": real}, f, indent=2, ensure_ascii=False, default=float)
    return path

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fit", action="store_true")
    args = ap.parse_args()
    played = m.prepare(load_rows())
    logging.info(f"oynanmış satır: {len(played)}, oyuncu: {played['player_key'].nunique()}, "
                 f"tarih: {played['game_date'].min().date()}..{played['game_date'].max().date()}")
    params = m.load_params() if args.no_fit else fit(played, m.DEFAULT_PARAMS)
    if not args.no_fit:
        m.save_params(params)
    ev, feats = evaluate(played, params)
    real = real_line_eval(feats, params)
    path = write_report(params, ev, real)
    print(json.dumps({"params": params, "evaluation": {k: v for k, v in ev.items()}, "real_lines": real},
                     indent=1, ensure_ascii=False, default=float))
    logging.info(f"rapor: {path}")

if __name__ == "__main__":
    main()
