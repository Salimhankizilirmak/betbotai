"""
Futbol maç modeli: zaman ağırlıklı Dixon-Coles (lig bazında).

    log λ_ev  = μ + ev_avantajı + hücum_ev  + savunma_dep
    log λ_dep = μ +               hücum_dep + savunma_ev
    P(x, y)   = τ(x, y; λ, γ, ρ) · Poisson(x; λ) · Poisson(y; γ)        (τ: 0-0, 1-0, 0-1, 1-1 düzeltmesi)

- Eski maçların ağırlığı exp(-ξ · gün) ile azalır.
- Hücum/savunma değerlerine L2 cezası (κ): az maçı olan (lige yeni çıkan) takımlar ortalamaya çekilir.
- Skor matrisinden 1X2, çifte şans, 1.5/2.5/3.5 alt-üst ve KG var/yok olasılıkları türetilir.
"""
import json
import os

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

PARAMS_PATH = os.path.join(os.path.dirname(__file__), "football_dc_params.json")
MAX_GOALS = 10
DEFAULT_PARAMS = {"version": "football-dc-v1-default", "xi": 0.0019, "kappa": 2.0, "window_days": 3 * 365,
                  "blend_w": {"1x2": 0.5, "ou25": 0.5}}

def load_params(path=PARAMS_PATH):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_PARAMS

def save_params(params, path=PARAMS_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=False)

def _tau(x, y, lam, gam, rho):
    t = np.ones_like(lam)
    t = np.where((x == 0) & (y == 0), 1 - lam * gam * rho, t)
    t = np.where((x == 0) & (y == 1), 1 + lam * rho, t)
    t = np.where((x == 1) & (y == 0), 1 + gam * rho, t)
    t = np.where((x == 1) & (y == 1), 1 - rho, t)
    return t

class DixonColes:
    def __init__(self, xi=0.0019, kappa=2.0):
        self.xi, self.kappa = xi, kappa
        self.teams, self.idx = [], {}
        self.theta = None  # [mu, home, rho, att(n), def(n)]

    def fit(self, matches, as_of, window_days=3 * 365, warm_start=None):
        """matches: home_team, away_team, fthg, ftag, match_date (sadece as_of'tan ÖNCEKİ maçlar kullanılır)."""
        as_of = pd.Timestamp(as_of)
        m = matches[(matches["match_date"] < as_of) & (matches["match_date"] >= as_of - pd.Timedelta(days=window_days))]
        m = m.dropna(subset=["fthg", "ftag"])
        if len(m) < 50:
            raise ValueError(f"yetersiz maç ({len(m)})")
        self.teams = sorted(set(m["home_team"]) | set(m["away_team"]))
        self.idx = {t: i for i, t in enumerate(self.teams)}
        n = len(self.teams)
        hi = m["home_team"].map(self.idx).values
        ai = m["away_team"].map(self.idx).values
        x = m["fthg"].astype(int).values
        y = m["ftag"].astype(int).values
        days = (as_of - m["match_date"]).dt.days.values
        w = np.exp(-self.xi * days)
        kappa = self.kappa

        def nll(theta):
            mu, home, rho = theta[0], theta[1], theta[2]
            att, dfn = theta[3:3 + n], theta[3 + n:]
            lam = np.exp(mu + home + att[hi] + dfn[ai])
            gam = np.exp(mu + att[ai] + dfn[hi])
            tau = np.clip(_tau(x, y, lam, gam, rho), 1e-10, None)
            ll = np.log(tau) + (x * np.log(lam) - lam) + (y * np.log(gam) - gam)
            return -(w * ll).sum() + kappa * (np.sum(att ** 2) + np.sum(dfn ** 2))

        x0 = np.zeros(3 + 2 * n)
        x0[0], x0[1] = np.log(1.3), 0.25
        if warm_start is not None:
            prev_teams, prev_theta = warm_start
            x0[:3] = prev_theta[:3]
            pidx = {t: i for i, t in enumerate(prev_teams)}
            pn = len(prev_teams)
            for t, i in self.idx.items():
                if t in pidx:
                    x0[3 + i] = prev_theta[3 + pidx[t]]
                    x0[3 + n + i] = prev_theta[3 + pn + pidx[t]]
        bounds = [(None, None), (-1, 1), (-0.3, 0.3)] + [(-3, 3)] * (2 * n)
        res = minimize(nll, x0, method="L-BFGS-B", bounds=bounds)
        self.theta = res.x
        self.as_of = as_of
        return self

    def rates(self, home, away):
        """(λ_ev, λ_dep); bilinmeyen (yeni) takım ortalama (0) güçle varsayılır."""
        n = len(self.teams)
        mu, h = self.theta[0], self.theta[1]
        att = lambda t: self.theta[3 + self.idx[t]] if t in self.idx else 0.0
        dfn = lambda t: self.theta[3 + n + self.idx[t]] if t in self.idx else 0.0
        return float(np.exp(mu + h + att(home) + dfn(away))), float(np.exp(mu + att(away) + dfn(home)))

    def score_matrix(self, home, away, max_goals=MAX_GOALS):
        lam, gam = self.rates(home, away)
        rho = self.theta[2]
        g = np.arange(max_goals + 1)
        M = np.outer(poisson.pmf(g, lam), poisson.pmf(g, gam))
        M[0, 0] *= 1 - lam * gam * rho
        M[0, 1] *= 1 + lam * rho
        M[1, 0] *= 1 + gam * rho
        M[1, 1] *= 1 - rho
        return M / M.sum()

    def known(self, team):
        return team in self.idx

def market_probs(M):
    """Skor matrisinden marketler."""
    g = np.arange(M.shape[0])
    total = g[:, None] + g[None, :]
    home, draw, away = np.tril(M, -1).sum(), np.trace(M), np.triu(M, 1).sum()
    out = {"home": home, "draw": draw, "away": away,
           "1X": home + draw, "X2": draw + away, "12": home + away,
           "btts_yes": M[1:, 1:].sum()}
    out["btts_no"] = 1 - out["btts_yes"]
    for line in (1.5, 2.5, 3.5):
        over = M[total > line].sum()
        out[f"over_{line}"], out[f"under_{line}"] = over, 1 - over
    return {k: float(v) for k, v in out.items()}

def blend(p_model, p_market, w):
    """Model ve piyasa olasılıklarının ağırlıklı ortalaması (piyasa sakatlık/kadro bilgisini içerir)."""
    p = w * np.asarray(p_model, float) + (1 - w) * np.asarray(p_market, float)
    return p / p.sum()
