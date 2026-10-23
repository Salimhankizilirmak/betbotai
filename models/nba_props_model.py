"""
NBA oyuncu prop projeksiyon modeli (PTS / REB / AST).

    beklenen değer  μ = dakika projeksiyonu × dakika başına üretim × rakip düzeltmesi
    dağılım         X ~ NegatifBinom(μ, θ_stat)   ->   P(üst), P(alt)

- Dakika: son maçların üssel ağırlıklı ortalaması (EWMA) ile sezon ortalamasının karışımı; back-to-back düzeltmesi.
- Üretim oranı: son maçların dakika ağırlıklı oranı ile uzun dönem oranının (bu sezon, az veri varsa geçen
  sezon / lig ortalaması) karışımı — küçük örneklerde ortalamaya çekme (shrinkage).
- Rakip: rakibin son 15 maçta oyunculara izin verdiği toplam stat / lig ortalaması (kısmi uygulanır).

Aynı özellik kodu hem backtest'te (backtest/nba_props_bt.py, walk-forward) hem canlı tahminde kullanılır;
her satırın özellikleri sadece o maçtan ÖNCEKİ verilerden hesaplanır.
"""
import json
import os

import numpy as np
import pandas as pd
from scipy.stats import nbinom

STATS = ("pts", "reb", "ast")
PARAMS_PATH = os.path.join(os.path.dirname(__file__), "nba_props_params.json")
OPP_WINDOW = 15
LONG_RATE_PRIOR_MIN = 300.0  # bu sezonki dakika bundan azsa geçen sezon / lig oranıyla harmanlanır

DEFAULT_PARAMS = {
    "version": "nba-props-v1-default",
    "min_halflife": 5.0, "min_gamma": 0.75, "b2b_factor": 0.97,
    "rate": {s: {"halflife": 10.0, "beta": 0.5, "alpha": 0.5} for s in STATS},
    "theta": {"pts": 12.0, "reb": 15.0, "ast": 10.0},
}

def load_params(path=PARAMS_PATH):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_PARAMS

def save_params(params, path=PARAMS_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=False)

# ---------------------------------------------------------------- veri hazırlığı
def _as_ns(col):
    """Tarihleri tek bir hassasiyete çevirir (merge_asof farklı datetime birimlerini birleştirmez)."""
    return pd.to_datetime(col).astype("datetime64[ns]")

def prepare(rows):
    """nba_player_games satırları (list[dict] veya DataFrame) -> oynanmış maçlar, tarih sıralı."""
    df = pd.DataFrame(rows).copy()
    if df.empty:
        return df
    df["game_date"] = _as_ns(df["game_date"])
    df = df[(~df["dnp"].astype(bool)) & (df["minutes"].fillna(0) > 0)].copy()
    for c in ("minutes",) + STATS:
        df[c] = df[c].astype(float)
    return df.sort_values(["player_key", "game_date"]).reset_index(drop=True)

def opponent_allowed(played):
    """
    Her takımın her maçta rakip oyunculara izin verdiği toplam stat (takım = opponent_abbr).
    Döner: (team_asof, league_asof) — maç sonrası kayan ortalamalar; merge_asof ile 'maçtan önce' okunur.
    """
    per_game = (played.groupby(["game_id", "game_date", "opponent_abbr"], as_index=False)[list(STATS)].sum()
                .rename(columns={"opponent_abbr": "team"})
                .sort_values(["team", "game_date"]))
    team = per_game.copy()
    for s in STATS:
        team[f"allow_{s}"] = team.groupby("team")[s].transform(lambda x: x.rolling(OPP_WINDOW, min_periods=3).mean())
    team = team[["team", "game_date"] + [f"allow_{s}" for s in STATS]].dropna()

    daily = per_game.groupby("game_date")[list(STATS)].mean().sort_index()
    league = daily.rolling(60, min_periods=5).mean().rename(columns={s: f"league_{s}" for s in STATS}).dropna()
    return team.sort_values("game_date"), league.reset_index().sort_values("game_date")

def _ewm_shift(g, col, halflife):
    return g[col].transform(lambda x: x.shift().ewm(halflife=halflife, min_periods=1).mean())

def add_features(df, params, opp_tables=None):
    """
    df: hedef satırlar + geçmiş (aynı oyuncunun önceki maçları). Hedef satırların stat kolonları NaN olabilir
    (canlı tahmin). Her satır için sadece önceki satırlardan özellik hesaplar.
    """
    df = df.copy()
    df["game_date"] = _as_ns(df["game_date"])
    df = df.sort_values(["player_key", "game_date"]).reset_index(drop=True)
    g = df.groupby("player_key", sort=False)

    df["n_prev"] = g.cumcount()
    df["prev_date"] = g["game_date"].shift()
    df["b2b"] = (df["game_date"] - df["prev_date"]).dt.days.eq(1)

    # dakika
    df["min_ewm"] = _ewm_shift(g, "minutes", params["min_halflife"])
    gs = df.groupby(["player_key", "season"], sort=False)
    df["season_min_sum"] = gs["minutes"].transform(lambda x: x.shift().fillna(0).cumsum())
    df["season_gp"] = gs.cumcount()
    df["season_min_avg"] = (df["season_min_sum"] / df["season_gp"].replace(0, np.nan))
    df["min_long"] = df["season_min_avg"].fillna(df["min_ewm"])
    df["min_proj"] = params["min_gamma"] * df["min_ewm"] + (1 - params["min_gamma"]) * df["min_long"]
    df.loc[df["b2b"], "min_proj"] *= params["b2b_factor"]

    # rakip düzeltmesi tabloları (maçtan önceki değer)
    if opp_tables is None:
        opp_tables = opponent_allowed(df.dropna(subset=["minutes"]))
    team_tbl, league_tbl = opp_tables
    df = df.sort_values("game_date")
    df = pd.merge_asof(df, team_tbl.rename(columns={"team": "opponent_abbr"}), on="game_date", by="opponent_abbr",
                       allow_exact_matches=False, direction="backward")
    df = pd.merge_asof(df, league_tbl, on="game_date", allow_exact_matches=False, direction="backward")
    df = df.sort_values(["player_key", "game_date"]).reset_index(drop=True)
    g = df.groupby("player_key", sort=False)
    gs = df.groupby(["player_key", "season"], sort=False)

    played_mask = df["minutes"].notna()
    for s in STATS:
        p = params["rate"][s]
        # son maçların dakika ağırlıklı oranı
        num = _ewm_shift(g, s, p["halflife"])
        den = _ewm_shift(g, "minutes", p["halflife"])
        df[f"rate_recent_{s}"] = num / den.replace(0, np.nan)
        # bu sezon kümülatif oran; az dakika varsa tüm geçmişin oranıyla harmanla
        s_sum = gs[s].transform(lambda x: x.shift().fillna(0).cumsum())
        rate_season = s_sum / df["season_min_sum"].replace(0, np.nan)
        all_s = g[s].transform(lambda x: x.shift().fillna(0).cumsum())
        all_m = g["minutes"].transform(lambda x: x.shift().fillna(0).cumsum())
        rate_all = all_s / all_m.replace(0, np.nan)
        w = (df["season_min_sum"] / (df["season_min_sum"] + LONG_RATE_PRIOR_MIN)).fillna(0)
        df[f"rate_long_{s}"] = (w * rate_season.fillna(0) + (1 - w) * rate_all).fillna(rate_all)
        df[f"rate_{s}"] = (p["beta"] * df[f"rate_recent_{s}"] + (1 - p["beta"]) * df[f"rate_long_{s}"])
        # rakip faktörü
        raw = (df[f"allow_{s}"] / df[f"league_{s}"]).fillna(1.0).clip(0.75, 1.35)
        df[f"opp_factor_{s}"] = 1 + p["alpha"] * (raw - 1)
        df[f"mu_{s}"] = df["min_proj"] * df[f"rate_{s}"] * df[f"opp_factor_{s}"]
    df["is_target_played"] = played_mask
    return df

# ---------------------------------------------------------------- olasılık
def prob_over(mu, line, theta):
    """P(X > line) — çizgiler .5 olduğu için X >= floor(line)+1. mu/line dizi olabilir."""
    mu = np.maximum(np.asarray(mu, dtype=float), 1e-6)
    k = np.floor(np.asarray(line, dtype=float))
    p = theta / (theta + mu)
    return nbinom.sf(k, theta, p)

def prob_under(mu, line, theta):
    """P(X < line). Tam sayı çizgide (ör. 20) push olasılığı ikisinden de düşülür."""
    mu = np.maximum(np.asarray(mu, dtype=float), 1e-6)
    line = np.asarray(line, dtype=float)
    p = theta / (theta + mu)
    k = np.ceil(line) - 1
    return nbinom.cdf(k, theta, p)

def nb_logpmf(x, mu, theta):
    mu = np.maximum(np.asarray(mu, dtype=float), 1e-6)
    return nbinom.logpmf(np.asarray(x), theta, theta / (theta + mu))

# ---------------------------------------------------------------- canlı tahmin
class PropModel:
    """Canlı kullanım: geçmiş satırları bir kez yükle, sonra her prop için project() çağır."""

    def __init__(self, history_rows, params=None):
        self.params = params or load_params()
        self.history = prepare(history_rows)
        self.opp_tables = opponent_allowed(self.history) if not self.history.empty else None

    def project(self, player_key, game_date, opponent_abbr, is_home=None, season=None):
        """Bir oyuncunun yaklaşan maçı için {mu_pts, mu_reb, mu_ast, min_proj, n_prev} veya None."""
        hist = self.history[self.history["player_key"] == player_key]
        if hist.empty or self.opp_tables is None:
            return None
        target = {c: np.nan for c in hist.columns}
        target.update({"player_key": player_key, "game_date": pd.Timestamp(game_date), "opponent_abbr": opponent_abbr,
                       "season": season or hist["season"].iloc[-1], "game_id": "upcoming", "is_home": is_home,
                       "minutes": np.nan, **{s: np.nan for s in STATS}})
        df = pd.concat([hist, pd.DataFrame([target])], ignore_index=True)
        feats = add_features(df, self.params, self.opp_tables)
        row = feats[feats["game_id"] == "upcoming"].iloc[-1]
        out = {"n_prev": int(row["n_prev"]), "min_proj": float(row["min_proj"]),
               "team_abbr": hist["team_abbr"].iloc[-1], "last_game": str(hist["game_date"].iloc[-1].date())}
        for s in STATS:
            out[f"mu_{s}"] = float(row[f"mu_{s}"]) if pd.notna(row[f"mu_{s}"]) else None
            out[f"opp_factor_{s}"] = float(row[f"opp_factor_{s}"])
        return out

    def probabilities(self, stat, mu, line):
        theta = self.params["theta"][stat]
        return float(prob_over(mu, line, theta)), float(prob_under(mu, line, theta))
