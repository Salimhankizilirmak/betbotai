"""
Bahis oranlarından marjı (vig) çıkarıp "adil" olasılıkları hesaplar.

Bir marketin tüm seçeneklerinin ondalık oranları verilir (örn. 1X2 için [ev, beraberlik, deplasman]).
- multiplicative: 1/oran değerlerini toplamına böler (en basit; favori/sürpriz farkını düzeltmez)
- power: p_i = q_i^k, sum(p_i) = 1 olacak k bulunur (iki seçenekli props/alt-üst için önerilen)
- shin: içeriden bilgi modeline dayanır; sürpriz (yüksek oran) tarafındaki fazla marjı düzeltir (1X2 için önerilen)
"""
import math

def implied_probabilities(prices):
    if not prices or any(p is None or p <= 1.0 for p in prices):
        raise ValueError(f"Geçersiz oranlar: {prices}")
    return [1.0 / p for p in prices]

def overround(prices):
    """Bahis şirketi marjı: 0.05 = %5."""
    return sum(implied_probabilities(prices)) - 1.0

def multiplicative(prices):
    q = implied_probabilities(prices)
    s = sum(q)
    return [x / s for x in q]

def _bisect(f, lo, hi, tol=1e-12, max_iter=200):
    flo = f(lo)
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        fmid = f(mid)
        if abs(fmid) < tol or hi - lo < tol:
            return mid
        if (fmid > 0) == (flo > 0):
            lo, flo = mid, fmid
        else:
            hi = mid
    return (lo + hi) / 2

def power(prices):
    q = implied_probabilities(prices)
    if abs(sum(q) - 1.0) < 1e-12:
        return q
    # sum(q^k) k'ya göre azalan; marj pozitifse k > 1, negatifse k < 1
    k = _bisect(lambda k: sum(x ** k for x in q) - 1.0, 0.01, 100.0)
    return [x ** k for x in q]

def shin(prices):
    q = implied_probabilities(prices)
    s = sum(q)
    if s <= 1.0:
        return multiplicative(prices)

    def probs(z):
        return [(math.sqrt(z * z + 4 * (1 - z) * x * x / s) - z) / (2 * (1 - z)) for x in q]

    z = _bisect(lambda z: sum(probs(z)) - 1.0, 0.0, 0.999)
    return probs(z)

METHODS = {"multiplicative": multiplicative, "power": power, "shin": shin}

def devig(prices, method="power"):
    """Marjsız olasılıklar; sıralama verilen oranlarla aynıdır."""
    return METHODS[method](prices)

def default_method(n_outcomes):
    """Varsayılan yöntem: 3 seçenekli (1X2) marketlerde shin, iki seçeneklide power."""
    return "shin" if n_outcomes >= 3 else "power"
