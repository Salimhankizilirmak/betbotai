"""Beklenen değer, edge ve kombine (kupon) hesapları."""
import math
from collections import defaultdict
from market.devig import devig, default_method

SHARP_BOOKMAKERS = ("pinnacle",)  # Varsa referans "adil" olasılık bu şirketten alınır

def expected_value(p, odds):
    """1 birimlik bahsin beklenen kârı. 0.05 = %5 pozitif beklenti."""
    return p * odds - 1.0

def edge(p_model, p_fair):
    """Modelin piyasanın marjsız olasılığına göre farkı (olasılık puanı)."""
    return p_model - p_fair

def kelly_fraction(p, odds):
    b = odds - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (b * p - (1 - p)) / b)

def parlay_odds(odds_list):
    return math.prod(odds_list)

def parlay_probability(probs):
    """Bağımsız seçimler varsayımıyla kuponun tutma olasılığı (aynı maçtan seçim alınmaz)."""
    return math.prod(probs)

def parlay_ev(probs, odds_list):
    return parlay_probability(probs) * parlay_odds(odds_list) - 1.0

def fair_probabilities_by_bookmaker(market_rows):
    """
    market_rows: aynı event + market + point için snapshot satırları
    (dict: bookmaker, selection, price). Her şirket için marjsız olasılık döner:
    {bookmaker: {selection: p}}
    """
    by_book = defaultdict(dict)
    for r in market_rows:
        by_book[r["bookmaker"]][r["selection"]] = r["price"]
    result = {}
    for book, sel_prices in by_book.items():
        sels = list(sel_prices)
        prices = [sel_prices[s] for s in sels]
        if len(prices) < 2 or any(p is None or p <= 1.0 for p in prices):
            continue
        probs = devig(prices, default_method(len(prices)))
        result[book] = dict(zip(sels, probs))
    return result

def consensus_fair_probability(market_rows):
    """
    Referans adil olasılık: keskin şirket (Pinnacle) varsa onunki, yoksa tüm şirketlerin
    marjsız olasılıklarının ortalaması. {selection: p}
    """
    per_book = fair_probabilities_by_bookmaker(market_rows)
    for sharp in SHARP_BOOKMAKERS:
        if sharp in per_book:
            return per_book[sharp]
    if not per_book:
        return {}
    selections = set().union(*per_book.values())
    out = {}
    for s in selections:
        vals = [b[s] for b in per_book.values() if s in b]
        out[s] = sum(vals) / len(vals)
    total = sum(out.values())
    return {s: p / total for s, p in out.items()}

def best_prices(market_rows):
    """Her seçim için şirketler arasındaki en yüksek oran: {selection: (price, bookmaker)}"""
    best = {}
    for r in market_rows:
        p = r["price"]
        if p and (r["selection"] not in best or p > best[r["selection"]][0]):
            best[r["selection"]] = (p, r["bookmaker"])
    return best
