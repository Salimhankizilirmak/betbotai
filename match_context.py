"""
Maç analizi için GERÇEK veriler (LLM'e verilir, panelde tablo olarak gösterilir). Hiçbir rakam uydurulmaz:

- Futbol (football_matches): lig sırası/puan, son 5 maç, ev/deplasman formu, maç başı gol, isabetli şut, xG, ikili maçlar
- Piyasa (Odds API + odds_snapshots): Pinnacle'ın tüm sonuçlar için marjsız olasılıkları, seçimin oranı,
  Pinnacle'da açılıştan bu yana oran hareketi
"""
import logging
import time

import pandas as pd

from market.devig import devig

_cache = {"df": None, "ts": 0.0}

def _matches(max_age=3600):
    """Son ~14 ayın maçları (xG dahil); süreç içinde 1 saat önbellekli."""
    if _cache["df"] is None or time.time() - _cache["ts"] > max_age:
        from bet_manager import get_db_connection
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT league, season, match_date, home_team, away_team, fthg, ftag, home_sot, away_sot,
                       (odds->>'HxG')::float AS hxg, (odds->>'AxG')::float AS axg
                FROM football_matches WHERE fthg IS NOT NULL AND match_date > NOW() - INTERVAL '430 days'
            """)
            df = pd.DataFrame([dict(r) for r in cur.fetchall()])
        if not df.empty:
            df["match_date"] = pd.to_datetime(df["match_date"])
        _cache.update(df=df, ts=time.time())
    return _cache["df"]

def _resolve(team, league):
    from data_loader import resolve_team_name
    df = _matches()
    pool = df[df["league"] == league] if league else df
    if pool.empty:
        return None, None
    name = resolve_team_name(team, pd.concat([pool["home_team"], pool["away_team"]]).unique(),
                             min_score=0.6 if league else 0.75)
    if not name:
        return None, None
    if not league:  # Avrupa kupası: takımın kendi yerel ligi
        rows = df[(df["home_team"] == name) | (df["away_team"] == name)]
        league = rows.sort_values("match_date")["league"].iloc[-1]
    return name, league

def _standings(league, season):
    df = _matches()
    m = df[(df["league"] == league) & (df["season"] == season)]
    table = {}
    for r in m.itertuples():
        for team, gf, ga in ((r.home_team, r.fthg, r.ftag), (r.away_team, r.ftag, r.fthg)):
            t = table.setdefault(team, {"p": 0, "pts": 0, "gf": 0, "ga": 0})
            t["p"] += 1; t["gf"] += gf; t["ga"] += ga
            t["pts"] += 3 if gf > ga else 1 if gf == ga else 0
    order = sorted(table, key=lambda k: (-table[k]["pts"], -(table[k]["gf"] - table[k]["ga"]), -table[k]["gf"]))
    return {team: {**table[team], "pos": i + 1, "n_teams": len(order)} for i, team in enumerate(order)}

def _form(rows, team, n=5):
    rows = rows.sort_values("match_date").tail(n)
    if rows.empty:
        return None
    res, gf, ga, sf, sa, xf, xa, nx = [], 0, 0, 0, 0, 0.0, 0.0, 0
    for r in rows.itertuples():
        home = r.home_team == team
        f, a = (r.fthg, r.ftag) if home else (r.ftag, r.fthg)
        gf += f; ga += a
        sf += (r.home_sot if home else r.away_sot) or 0
        sa += (r.away_sot if home else r.home_sot) or 0
        if pd.notna(r.hxg) and pd.notna(r.axg):
            xf += r.hxg if home else r.axg; xa += r.axg if home else r.hxg; nx += 1
        res.append("G" if f > a else "B" if f == a else "M")
    k = len(rows)
    counts = f"{res.count('G')} galibiyet, {res.count('B')} beraberlik, {res.count('M')} mağlubiyet"
    out = {"n": k, "results": f"{'-'.join(res)} ({counts})", "gf": gf / k, "ga": ga / k,
           "sot_for": sf / k, "sot_against": sa / k}
    if nx:
        out.update(xg_for=xf / nx, xg_against=xa / nx)
    return out

def team_facts(team, league, venue):
    """venue: 'home' | 'away' — ev sahibinin iç saha, deplasmanın dış saha formu da eklenir."""
    name, lg = _resolve(team, league)
    if not name:
        return None
    df = _matches()
    played = df[(df["home_team"] == name) | (df["away_team"] == name)]
    season = played.sort_values("match_date")["season"].iloc[-1]
    st = _standings(lg, season).get(name)
    venue_rows = played[played["home_team"] == name] if venue == "home" else played[played["away_team"] == name]
    return {"name": name, "league": lg, "standing": st, "form": _form(played, name),
            "venue_form": _form(venue_rows, name)}

def h2h(home, away, league, n=4):
    h, _ = _resolve(home, league)
    a, _ = _resolve(away, league)
    if not h or not a:
        return []
    df = _matches()
    m = df[((df["home_team"] == h) & (df["away_team"] == a)) | ((df["home_team"] == a) & (df["away_team"] == h))]
    return [f"{r.match_date:%d.%m.%Y} {r.home_team} {r.fthg}-{r.ftag} {r.away_team}"
            for r in m.sort_values("match_date").tail(n).itertuples()]

def _pinnacle_move(event_id, selection, point=None):
    """Pinnacle'da bu seçimin ilk ve son kayıtlı oranı (odds_snapshots)."""
    try:
        from bet_manager import get_db_connection
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT price, captured_at FROM odds_snapshots
                WHERE event_id = %s AND bookmaker = 'pinnacle' AND selection = %s
                  AND (%s::real IS NULL OR point = %s::real)
                ORDER BY captured_at
            """, (event_id, selection, point, point))
            rows = cur.fetchall()
        if len(rows) >= 2:
            return {"open": rows[0]["price"], "now": rows[-1]["price"], "since": rows[0]["captured_at"]}
    except Exception as e:
        logging.warning(f"oran hareketi okunamadı: {e}")
    return None

def market_facts(event, res):
    import value_bets as vb
    out = {}
    h2h_books = vb._outcomes_1x2(event)
    if "pinnacle" in h2h_books:
        names = [n for n in ("HOME_WIN", "DRAW", "AWAY_WIN") if n in h2h_books["pinnacle"]]
        fair = devig([h2h_books["pinnacle"][n] for n in names], "shin" if len(names) == 3 else "power")
        out["fair_1x2"] = dict(zip(names, fair))
    totals = vb._outcomes_totals(event)
    for pt in sorted(totals):
        if "pinnacle" in totals[pt]:
            p = devig([totals[pt]["pinnacle"]["OVER"], totals[pt]["pinnacle"]["UNDER"]], "power")
            out.setdefault("fair_totals", {})[pt] = {"OVER": p[0], "UNDER": p[1]}
    c = res["best"] or res["top"]
    out["pick"] = c
    if c["market"] == "h2h":
        sel = {"HOME_WIN": event["home_team"], "AWAY_WIN": event["away_team"], "DRAW": "Draw"}[c["pick"]]
        out["move"] = _pinnacle_move(event["id"], sel)
    else:
        side, pt = c["pick"].split()
        out["move"] = _pinnacle_move(event["id"], side.title(), float(pt))
    return out

LABEL = {"HOME_WIN": "1", "DRAW": "X", "AWAY_WIN": "2"}

def _move_text(mv):
    """Oran hareketini cümleyle yazar (LLM yönü ters okumasın)."""
    if mv["now"] < mv["open"]:
        return (f"📈 Pinnacle'da bu seçimin oranı {mv['open']:.2f}'den {mv['now']:.2f}'e DÜŞTÜ "
                f"(piyasa bu sonuca daha fazla olasılık veriyor)")
    return (f"📉 Pinnacle'da bu seçimin oranı {mv['open']:.2f}'den {mv['now']:.2f}'e YÜKSELDİ "
            f"(piyasa bu sonuca daha az olasılık veriyor)")

def _team_line(icon, t):
    if not t:
        return None
    parts = [f"{icon} {t['name']}"]
    st = t.get("standing")
    if st:
        parts.append(f"ligde {st['pos']}./{st['n_teams']} ({st['pts']} puan, {st['p']} maç, averaj {st['gf'] - st['ga']:+d})")
    f = t.get("form")
    if f:
        s = (f"son {f['n']}: {f['results']}, maç başı {f['gf']:.1f} gol attı / {f['ga']:.1f} gol yedi, "
             f"maç başı isabetli şut {f['sot_for']:.1f} çekti / {f['sot_against']:.1f} rakibe izin verdi")
        if "xg_for" in f:
            s += f", maç başı xG {f['xg_for']:.2f} üretti / {f['xg_against']:.2f} rakibe izin verdi"
        parts.append(s)
    vf = t.get("venue_form")
    if vf:
        parts.append(f"{'iç sahada' if icon == '🏠' else 'deplasmanda'} son {vf['n']}: {vf['results']}, "
                     f"maç başı {vf['gf']:.1f} attı / {vf['ga']:.1f} yedi")
    return " · ".join(parts)

def football_context(event, res):
    """(facts dict, Türkçe metin bloğu)."""
    from leagues import ODDS_TO_STATS_MAP
    league = ODDS_TO_STATS_MAP.get(event.get("sport_key"))
    facts = {}
    try:
        facts["home"] = team_facts(event["home_team"], league, "home")
        facts["away"] = team_facts(event["away_team"], league, "away")
        facts["h2h"] = h2h(event["home_team"], event["away_team"], league)
    except Exception as e:
        logging.warning(f"takım verisi okunamadı ({event.get('home_team')}): {e}")
    try:
        facts["market"] = market_facts(event, res)
    except Exception as e:
        logging.warning(f"piyasa verisi okunamadı: {e}")
    return facts, render_text(event, facts)

def render_text(event, facts):
    lines = []
    m = facts.get("market") or {}
    if m.get("fair_1x2"):
        lines.append("📊 Piyasa (Pinnacle, marjsız): " + " · ".join(f"{LABEL[k]} %{v * 100:.1f}" for k, v in m["fair_1x2"].items())
                     + (f" | 2.5 Üst %{m['fair_totals'][2.5]['OVER'] * 100:.0f}" if 2.5 in (m.get("fair_totals") or {}) else ""))
    c = m.get("pick")
    if c:
        import value_bets as vb
        head = "💰 Seçim" if c["ev"] >= 0.02 else "🔍 En yakın aday (değer yok)"
        lines.append(f"{head}: {vb.pick_label(c['pick'])} @ {c['odds']:.2f} ({c['book']}) — Pinnacle {c['sharp_odds']:.2f}, "
                     f"adil olasılık %{c['p_fair'] * 100:.1f}, beklenen değer {c['ev'] * 100:+.1f}%")
    mv = m.get("move")
    if mv and abs(mv["now"] - mv["open"]) >= 0.01:
        lines.append(_move_text(mv))
    for icon, key in (("🏠", "home"), ("✈️", "away")):
        line = _team_line(icon, facts.get(key))
        if line:
            lines.append(line)
    if facts.get("h2h"):
        lines.append("🤝 Son karşılaşmalar: " + " | ".join(facts["h2h"]))
    return "\n".join(lines)

# ---------------------------------------------------------------- basketbol (NBA: ESPN tabloları, Euroleague: feed)
def _nba_team_games(team, n=10):
    import espn_nba
    import nba_data
    from seasons import nba_season
    with espn_nba._db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM nba_games WHERE completed AND season = %s ORDER BY game_date DESC""", (nba_season(),))
        rows = [dict(r) for r in cur.fetchall()]
    mine = [g for g in rows if nba_data.team_match(team, g["home_team"]) or nba_data.team_match(team, g["away_team"])][:n]
    out = []
    for g in mine:
        home = nba_data.team_match(team, g["home_team"])
        pf, pa = (g["home_score"], g["away_score"]) if home else (g["away_score"], g["home_score"])
        opp = g["away_abbr"] if home else g["home_abbr"]
        out.append({"date": g["game_date"], "home": home, "opp": opp, "pf": pf, "pa": pa, "won": pf > pa})
    return out

def nba_team_facts(team):
    import nba_data
    df = nba_data.fetch_nba_standings()
    row = nba_data._standings_row(df, team) if df is not None else None
    games = _nba_team_games(team)
    facts = {"name": team}
    if row is not None:
        facts.update(record=row["Record"], streak=row["CurrentStreak"], l10=row["L10"], abbr=row["TeamAbbr"])
    if games:
        k = len(games)
        last5 = ["G" if g["won"] else "M" for g in games[:5]]
        facts.update(n=k, results=f"{'-'.join(last5)} ({last5.count('G')} galibiyet, {last5.count('M')} mağlubiyet)",
                     pf=sum(g["pf"] for g in games) / k, pa=sum(g["pa"] for g in games) / k,
                     total=sum(g["pf"] + g["pa"] for g in games) / k)
    if facts.get("n", 0) < 5:  # sezon başı: geçen sezonun özeti de verilsin
        try:
            import espn_nba
            from seasons import nba_season
            prev = espn_nba.previous_season(nba_season())
            with espn_nba._db() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM nba_games WHERE completed AND season = %s", (prev,))
                pg = [dict(r) for r in cur.fetchall()]
            mine = [(g["home_score"], g["away_score"]) if nba_data.team_match(team, g["home_team"]) else (g["away_score"], g["home_score"])
                    for g in pg if nba_data.team_match(team, g["home_team"]) or nba_data.team_match(team, g["away_team"])]
            if mine:
                w = sum(1 for pf, pa in mine if pf > pa)
                facts["prev"] = {"season": prev, "w": w, "l": len(mine) - w,
                                 "pf": sum(pf for pf, _ in mine) / len(mine), "pa": sum(pa for _, pa in mine) / len(mine)}
        except Exception as e:
            logging.warning(f"geçen sezon özeti okunamadı ({team}): {e}")
    if facts.get("abbr"):
        try:
            import espn_nba
            facts["top"] = [f"{r['player_name']} {float(r['pts']):.1f} sayı/{float(r['reb']):.1f} rib/{float(r['ast']):.1f} ast"
                            for r in espn_nba.team_top_scorers(facts["abbr"])]
        except Exception:
            pass
    return facts

def _nba_line(icon, t):
    parts = [f"{icon} {t['name']}"]
    if t.get("record"):
        parts.append(f"{t['record']} (seri {t['streak']}, son 10: {t['l10']})")
    if t.get("n"):
        parts.append(f"son {min(5, t['n'])}: {t['results']}, son {t['n']} maçta {t['pf']:.1f} attı / {t['pa']:.1f} yedi "
                     f"(maç toplamı ort. {t['total']:.1f})")
    if t.get("prev"):
        p = t["prev"]
        parts.append(f"geçen sezon ({p['season']}, veritabanındaki {p['w'] + p['l']} maç): {p['w']}-{p['l']}, "
                     f"{p['pf']:.1f} attı / {p['pa']:.1f} yedi")
    if t.get("top"):
        parts.append("bu sezon kilit oyuncular: " + ", ".join(t["top"]))
    return " · ".join(parts)

def basketball_context(event, res=None):
    facts, lines = {}, []
    if res is not None:
        try:
            facts["market"] = market_facts(event, res)
        except Exception as e:
            logging.warning(f"piyasa verisi okunamadı: {e}")
    m = facts.get("market") or {}
    if m.get("fair_1x2"):
        f = m["fair_1x2"]
        line = f"📊 Piyasa (Pinnacle, marjsız): {event['home_team']} %{f.get('HOME_WIN', 0) * 100:.1f} · {event['away_team']} %{f.get('AWAY_WIN', 0) * 100:.1f}"
        if m.get("fair_totals"):
            pt = sorted(m["fair_totals"])[len(m["fair_totals"]) // 2]
            line += f" | toplam {pt:g}: Üst %{m['fair_totals'][pt]['OVER'] * 100:.0f}"
        lines.append(line)
    c = m.get("pick")
    if c and res and res.get("best"):
        import value_bets as vb
        lines.append(f"💰 Seçim: {vb.pick_label(c['pick'])} @ {c['odds']:.2f} ({c['book']}) — Pinnacle {c['sharp_odds']:.2f}, "
                     f"adil olasılık %{c['p_fair'] * 100:.1f}, beklenen değer {c['ev'] * 100:+.1f}%")
    mv = m.get("move")
    if mv and abs(mv["now"] - mv["open"]) >= 0.01:
        lines.append(_move_text(mv))
    try:
        if event.get("sport_key") == "basketball_nba":
            for icon, team in (("🏠", event["home_team"]), ("✈️", event["away_team"])):
                t = nba_team_facts(team)
                facts[icon] = t
                lines.append(_nba_line(icon, t))
            import nba_data
            h = nba_data.fetch_nba_h2h(event["home_team"], event["away_team"])
            if h and "henüz" not in h:
                lines.append(f"🤝 Bu sezon: {h}")
        elif "euroleague" in event.get("sport_key", ""):
            import asyncio as _a
            import euroleague_data as el
            for icon, team in (("🏠", event["home_team"]), ("✈️", event["away_team"])):
                stats = _a.run(el.get_euroleague_team_stats(team))
                if stats and "No Euroleague" not in stats:
                    lines.append(f"{icon} {team}: {stats}")
    except Exception as e:
        logging.warning(f"basketbol verisi okunamadı ({event.get('home_team')}): {e}")
    return facts, "\n".join(lines)
