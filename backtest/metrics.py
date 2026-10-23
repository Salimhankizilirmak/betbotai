"""
Model ve bahis performansı metrikleri. Go/no-go kararları bunlarla verilir.

- log_loss / brier: olasılık kalitesi (düşük = iyi); piyasanın marjsız olasılığıyla karşılaştırılır
- calibration_table / expected_calibration_error: "%60 dediğimiz olaylar ~%60 gerçekleşiyor mu?"
- roi, clv: bahis sonuçları; CLV (kapanış oranını yenmek) şanstan bağımsız en güvenilir edge göstergesi
"""
import math
import random

EPS = 1e-15

def log_loss(probs, outcomes):
    """
    İkili: probs=[p, ...], outcomes=[0/1, ...]
    Çok sınıflı: probs=[[p1, p2, p3], ...], outcomes=[gerçekleşen sınıf indeksi, ...]
    """
    if len(probs) != len(outcomes) or not probs:
        raise ValueError("probs ve outcomes aynı uzunlukta ve boş olmamalı")
    total = 0.0
    for p, y in zip(probs, outcomes):
        if isinstance(p, (list, tuple)):
            total -= math.log(max(p[y], EPS))
        else:
            total -= math.log(max(p, EPS)) if y else math.log(max(1 - p, EPS))
    return total / len(probs)

def brier(probs, outcomes):
    """İkili veya çok sınıflı Brier skoru (çok sınıflıda sınıflar üzerinden toplam)."""
    total = 0.0
    for p, y in zip(probs, outcomes):
        if isinstance(p, (list, tuple)):
            total += sum((pk - (1.0 if k == y else 0.0)) ** 2 for k, pk in enumerate(p))
        else:
            total += (p - y) ** 2
    return total / len(probs)

def calibration_table(probs, outcomes, bins=10):
    """İkili olasılıklar için eşit genişlikte dilimler: [{lo, hi, n, mean_p, freq}]"""
    buckets = [[] for _ in range(bins)]
    for p, y in zip(probs, outcomes):
        i = min(int(p * bins), bins - 1)
        buckets[i].append((p, y))
    table = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        table.append({
            "lo": i / bins, "hi": (i + 1) / bins, "n": len(b),
            "mean_p": sum(p for p, _ in b) / len(b),
            "freq": sum(y for _, y in b) / len(b),
        })
    return table

def expected_calibration_error(probs, outcomes, bins=10):
    n = len(probs)
    return sum(row["n"] / n * abs(row["mean_p"] - row["freq"]) for row in calibration_table(probs, outcomes, bins))

def bet_profit(odds, won, stake=1.0, void=False):
    if void:
        return 0.0
    return stake * (odds - 1.0) if won else -stake

def roi(bets):
    """bets: [{odds, won, stake?, void?}] -> toplam kâr / toplam stake (iadeler stake'e girmez)."""
    staked = sum(b.get("stake", 1.0) for b in bets if not b.get("void"))
    if staked == 0:
        return 0.0
    profit = sum(bet_profit(b["odds"], b["won"], b.get("stake", 1.0), b.get("void", False)) for b in bets)
    return profit / staked

def clv(taken_odds, closing_fair_prob):
    """Alınan oranın kapanıştaki adil olasılığa göre beklenen değeri. >0 ise kapanışı yendik."""
    return taken_odds * closing_fair_prob - 1.0

def clv_price_ratio(taken_odds, closing_odds):
    """Basit CLV: alınan oran / kapanış oranı - 1."""
    return taken_odds / closing_odds - 1.0

def profit_curve(profits):
    curve, total = [], 0.0
    for p in profits:
        total += p
        curve.append(total)
    return curve

def max_drawdown(profits):
    """Kümülatif kâr eğrisinde zirveden en büyük düşüş (birim cinsinden, pozitif sayı)."""
    peak, mdd, total = 0.0, 0.0, 0.0
    for p in profits:
        total += p
        peak = max(peak, total)
        mdd = max(mdd, peak - total)
    return mdd

def longest_losing_streak(results):
    """results: [True/False] (kazandı mı). İadeler (None) seriyi bozmaz."""
    best = cur = 0
    for r in results:
        if r is None:
            continue
        cur = 0 if r else cur + 1
        best = max(best, cur)
    return best

def bootstrap_ci(values, stat=lambda v: sum(v) / len(v), n_resamples=2000, alpha=0.10, seed=42):
    """Yüzdelik bootstrap güven aralığı (varsayılan %90). (alt, üst) döner."""
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    k = len(values)
    stats = sorted(stat([values[rng.randrange(k)] for _ in range(k)]) for _ in range(n_resamples))
    lo = stats[int((alpha / 2) * n_resamples)]
    hi = stats[min(int((1 - alpha / 2) * n_resamples), n_resamples - 1)]
    return (lo, hi)
