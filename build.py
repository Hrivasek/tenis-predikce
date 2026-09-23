#!/usr/bin/env python3
"""
Z dat (historie + ESPN) spočítá Elo, predikce a statistiky a zapíše je pro web.

  python3 build.py

Zapisuje:
  site/data/today.json     zápasy hlavní soutěže dnes a zítra (pražský čas) s pravděpodobnostmi
  site/data/ratings.json   Elo aktivních hráčů pro kalkulačku (celkové + podle povrchu)
  site/data/track.json     úspěšnost modelu: zpětný test 2023+ a živá bilance předzápasových tipů
  data/predictions.csv     log předzápasových predikcí (základ živé bilance, verzováno v gitu)
"""
import csv, json, math, os
from collections import defaultdict, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import elo, mapping

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "site", "data")
PRED_PATH = os.path.join(elo.DATA_DIR, "predictions.csv")
PRAGUE = ZoneInfo("Europe/Prague")
# Pod tolik zápasů (všech úrovní včetně challengerů a ITF) je Elo hráče nespolehlivé.
# Ověřeno na 2015+: hráče s 0–4 zápasy dělá model favoritem o 16 (ATP) až 28 (WTA) p. b. častěji,
# než odpovídá realitě; od 5 zápasů systematické přeceňování mizí. 10 = hranice s rezervou.
# (Bez challengerů v historii bylo potřeba 15 zápasů hlavní soutěže.)
LOW_DATA = 10
LONG_BREAK = 60                   # dní bez zápasu → štítek „dlouho nehrál“ (model neví o zraněních)
ODDS_PATH = os.path.join(elo.DATA_DIR, "odds.json")     # kurzy zadané z webu (přes GitHub API)
PRED_FIELDS = ["match_id", "tour", "start", "tourney", "round", "surface",
               "p1_id", "p1_name", "p2_id", "p2_name", "p1_elo", "p2_elo", "n1", "n2",
               "p1_prob", "predicted_at", "status", "winner", "score", "model"]


def parse_iso(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def local_day(iso):
    return parse_iso(iso).astimezone(PRAGUE).date()


def summary(ps):
    """ps: pravděpodobnosti, které model dal skutečnému vítězi."""
    n = len(ps)
    if not n:
        return {"n": 0}
    return {"n": n, "acc": sum(p > 0.5 for p in ps) / n,
            "logloss": sum(-math.log(max(p, 1e-9)) for p in ps) / n,
            "brier": sum((1 - p) ** 2 for p in ps) / n}


def calibration(ps):
    """Z pohledu favorita: předpověď 50–100 % (koše po 10 p. b.) vs. skutečná úspěšnost favorita."""
    b = defaultdict(lambda: [0, 0.0, 0])
    for p in ps:
        fav_p, fav_won = (p, 1) if p >= 0.5 else (1 - p, 0)
        i = min(int((fav_p - 0.5) * 10), 4)
        b[i][0] += 1; b[i][1] += fav_p; b[i][2] += fav_won
    return [{"from": 0.5 + i * 0.1, "to": 0.6 + i * 0.1, "n": n, "pred": s / n, "real": w / n}
            for i, (n, s, w) in sorted(b.items())]


def days_between(yyyymmdd, day):
    return (day - datetime.strptime(yyyymmdd, "%Y%m%d").date()).days


def player_info(model, pid, name, surface, day):
    last = model.last_date.get(pid)
    off = days_between(last, day) if last else None
    return {"name": name, "elo": round(model.blended(pid, surface)), "n": model.n[pid],
            "days_off": off, "long_break": off is not None and off >= LONG_BREAK}


def load_odds():
    if not os.path.exists(ODDS_PATH):
        return {}
    with open(ODDS_PATH, encoding="utf-8") as f:
        return json.load(f)


def odds_track(odds, preds):
    """Jak by dopadly sázky podle modelu proti zadaným kurzům: 1 jednotka na stranu
    s nejvyšší kladnou hodnotou (EV = p * kurz - 1). Skreč a kontumace = storno.
    Zápasy hráčů s málo daty se vyhodnocují zvlášť, aby nezkreslovaly výsledky."""
    entries = []
    for key, o in odds.items():
        tour, mid = key.split(":", 1)
        pr = preds.get((tour, mid))
        if not pr or not o.get("o1") or not o.get("o2"):
            continue
        p1 = float(pr["p1_prob"])
        ev1, ev2 = p1 * o["o1"] - 1, (1 - p1) * o["o2"] - 1
        side = 1 if ev1 >= ev2 else 2
        ev = max(ev1, ev2)
        late = bool(o.get("at")) and parse_iso(o["at"]) > parse_iso(pr["start"])
        e = {"key": key, "tour": tour, "start": pr["start"], "tourney": pr["tourney"],
             "p1": pr["p1_name"], "p2": pr["p2_name"], "p1_prob": p1, "o1": o["o1"], "o2": o["o2"],
             "book": o.get("book", ""), "margin": 1 / o["o1"] + 1 / o["o2"] - 1,
             "tip": side if ev > 0 else None, "ev": ev, "late": late,
             "low_data": min(int(pr["n1"]), int(pr["n2"])) < LOW_DATA,
             "model": pr["model"], "status": pr["status"], "profit": None}
        if e["tip"] and not late and pr["status"] == "final":
            won = pr["winner"] == (pr["p1_id"] if side == 1 else pr["p2_id"])
            e["profit"] = (o["o1"] if side == 1 else o["o2"]) - 1 if won else -1.0
        entries.append(e)
    entries.sort(key=lambda e: e["start"], reverse=True)

    def agg(min_ev, low_data=False):
        done = [e for e in entries if e["profit"] is not None and e["ev"] > min_ev
                and e["low_data"] == low_data and e["model"] == elo.MODEL_VERSION]
        n = len(done)
        profit = sum(e["profit"] for e in done)
        return {"min_ev": min_ev, "n": n, "wins": sum(e["profit"] > 0 for e in done),
                "profit": profit, "roi": profit / n if n else None}
    return {"entries": entries[:200], "total": len(entries),
            "tips_pending": sum(e["tip"] is not None and e["profit"] is None and e["status"] == "scheduled"
                                for e in entries),
            "by_threshold": [agg(t) for t in (0.0, 0.05, 0.10)],
            "low_data": agg(0.0, low_data=True),
            "other_models": sum(e["model"] != elo.MODEL_VERSION and e["profit"] is not None for e in entries)}


LEVELS = {"G": "Grand Slam", "M": "Masters", "A": "ATP 250/500", "F": "Finals", "D": "Davis/BJK Cup",
          "PM": "WTA 1000", "P": "WTA 500", "I": "WTA 250", "C": "Challenger/125", "L": ""}


def load_players(tour):
    path = os.path.join(elo.DATA_DIR, f"players_{tour}.csv")
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["player_id"]: r for r in csv.DictReader(f)}


def match_day(r):
    """Datum zápasu: z ESPN přesné, z historie jen začátek turnaje."""
    if r.get("_date"):
        return datetime.fromisoformat(r["_date"][:10]).date()
    d = r["tourney_date"]
    return datetime(int(d[:4]), int(d[4:6]), int(d[6:8])).date()


def level_label(r):
    lv = r.get("tourney_level", "")
    if r.get("round") in ("Q", "Q1", "Q2", "Q3", "Q4"):
        return "kvalifikace"
    if r.get("_source") == "espn":
        return ""
    if lv == "L":                              # nižší úrovně: podle id/úrovně z archivu
        return "Challenger/125" if r.get("_lvl") == "C" else "ITF/Futures"
    return LEVELS.get(lv, lv)


def details(model, tour, wanted, players, espn_country):
    """Detail k zápasům v rozpisu: vzájemné zápasy, posledních 10 zápasů, zatížení v posledních dnech.
    wanted: {match_id: (pid1, pid2, den zápasu)}"""
    pids = {p for a, b, _ in wanted.values() for p in (a, b)}
    pairs = {frozenset((a, b)) for a, b, _ in wanted.values()}
    last = defaultdict(lambda: deque(maxlen=12))
    h2h = defaultdict(list)
    for r in model.rows:
        w, l = r["winner_id"], r["loser_id"]
        if w not in pids and l not in pids:
            continue
        item = {"d": match_day(r).isoformat(), "exact": bool(r.get("_date")) and not r.get("_date_est"),
                "t": r["tourney_name"],
                "lvl": level_label(r), "r": r["round"], "s": r["_surface"], "sc": r["score"],
                "w": w, "wn": r["winner_name"], "ln": r["loser_name"]}
        if w in pids: last[w].append(item)
        if l in pids: last[l].append(item)
        if frozenset((w, l)) in pairs:
            h2h[frozenset((w, l))].append(item)

    def pinfo(pid, name, country, day):
        info = players.get(pid, {})
        ms = list(last[pid])
        recent = [m for m in ms if 0 <= (day - datetime.fromisoformat(m["d"]).date()).days <= 7]
        def load(days):
            sel = [m for m in recent if (day - datetime.fromisoformat(m["d"]).date()).days <= days]
            return {"m": len(sel), "sets": sum(len([t for t in m["sc"].split() if "-" in t]) for m in sel)}
        age = None
        if info.get("dob") and len(info["dob"]) == 8 and info["dob"] != "19000000":
            b = datetime.strptime(info["dob"], "%Y%m%d").date()
            age = round((day - b).days / 365.25, 1)
        return {"name": name, "hand": {"R": "pravák", "L": "levák"}.get(info.get("hand"), ""),
                "country": info.get("ioc") or country, "height": info.get("height") or "", "age": age,
                "load3": load(3), "load7": load(7),
                "exact": all(m["exact"] for m in recent),
                "last10": [{"d": m["d"], "ex": m["exact"], "t": m["t"], "lvl": m["lvl"], "r": m["r"], "s": m["s"], "sc": m["sc"],
                            "won": m["w"] == pid, "opp": m["ln"] if m["w"] == pid else m["wn"]}
                           for m in reversed(ms[-10:])]}
    out = {}
    for mid, (a, b, day, n1, n2) in ((k, v + (espn_country[k])) for k, v in wanted.items()):
        hh = h2h.get(frozenset((a, b)), [])
        out[f"{tour}:{mid}"] = {
            "h2h": [{"d": m["d"], "ex": m["exact"], "t": m["t"], "lvl": m["lvl"], "r": m["r"], "s": m["s"], "sc": m["sc"],
                     "win": 1 if m["w"] == a else 2} for m in reversed(hh)],
            "h2h_n": [sum(m["w"] == a for m in hh), sum(m["w"] == b for m in hh)],
            "p1": pinfo(a, *n1, day), "p2": pinfo(b, *n2, day)}
    return out


def elo_check(model, tour, active_since):
    """Porovnání našeho Elo s Elo žebříčkem Tennis Abstract (stejná ID hráčů).
    Stupnice se liší, proto náš rating převedeme na jejich lineární regresí a hlásíme velké odchylky."""
    path = os.path.join(elo.DATA_DIR, f"ta_elo_{tour}.csv")
    if not os.path.exists(path):
        return None
    pmap = elo.PLAYER_MAPS[tour]
    with open(path, newline="", encoding="utf-8") as f:
        ta = [r for r in csv.DictReader(f) if int(r["rank"]) <= 300]
    for r in ta:                        # TA žebříček odkazuje hráče jménem → párujeme jako ESPN
        r["player_id"] = r["player_id"] or pmap.resolve("TA:" + r["name"], r["name"])
    pairs = [(r, model.rating[r["player_id"]]) for r in ta
             if model.n.get(r["player_id"], 0) >= 30 and model.last_date.get(r["player_id"], "") >= active_since]
    if len(pairs) < 30:
        return None
    xs = [o for _, o in pairs]; ys = [float(r["elo"]) for r, _ in pairs]
    n = len(xs); mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs); sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    b_, a_ = sxy / sxx, my - sxy / sxx * mx
    res = [(r, o, a_ + b_ * o, float(r["elo"]) - (a_ + b_ * o)) for (r, o) in pairs]
    sd = math.sqrt(sum(d * d for *_, d in res) / (n - 2))
    limit = max(100.0, 2.5 * sd)
    ours_rank = {pid: i for i, pid in enumerate(sorted({r["player_id"] for r, _ in pairs},
                                                       key=lambda p: -model.rating[p]), 1)}
    flagged = sorted((x for x in res if abs(x[3]) >= limit), key=lambda x: -abs(x[3]))
    out = {"updated": ta[0]["updated"] if ta else "", "n": n, "corr": sxy / math.sqrt(sxx * syy),
           "sd": sd, "limit": limit,
           "flagged": [{"name": r["name"], "ta_elo": float(r["elo"]), "ta_rank": int(r["rank"]),
                        "ours_as_ta": round(e), "diff": round(d), "our_rank": ours_rank[r["player_id"]],
                        "n": model.n[r["player_id"]]} for r, o, e, d in flagged[:15]]}
    for x in out["flagged"]:
        print(f"  ⚠ {tour.upper()} {x['name']}: TA Elo {x['ta_elo']:.0f} (#{x['ta_rank']}), náš model odpovídá "
              f"{x['ours_as_ta']} (#{x['our_rank']}), rozdíl {x['diff']:+d}")
    return out


def load_predictions():
    if not os.path.exists(PRED_PATH):
        return {}
    with open(PRED_PATH, newline="", encoding="utf-8") as f:
        preds = {(r["tour"], r["match_id"]): r for r in csv.DictReader(f)}
    for r in preds.values():
        if not r.get("model"):          # starší řádky bez verze: podle času predikce
            r["model"] = max((v for v, m in elo.MODELS.items() if r["predicted_at"] >= m["since"]),
                             key=lambda v: elo.MODELS[v]["since"], default="v1")
    return preds


def save_predictions(preds):
    with open(PRED_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PRED_FIELDS)
        w.writeheader()
        w.writerows(sorted(preds.values(), key=lambda r: (r["start"], r["tour"], r["match_id"])))


def main():
    now = datetime.now(PRAGUE)
    today = now.date()
    with open(os.path.join(elo.DATA_DIR, "upcoming.json"), encoding="utf-8") as f:
        upcoming = json.load(f)
    preds = load_predictions()
    odds = load_odds()
    matches_out, ratings_out, backtest, details_out, checks = [], {}, {}, {}, {}

    for tour in ("atp", "wta"):
        log = []
        model, stats = elo.run(tour, log=log, keep_rows=True)
        wanted, wanted_meta = {}, {}
        pmap = elo.PLAYER_MAPS[tour]            # stejné párování jako při výpočtu Elo
        smap = mapping.SurfaceMap({t: elo.history(t) for t in ("atp", "wta")})

        # --- zpětný test (2023+) ---
        by_surface = defaultdict(list); by_year = defaultdict(list)
        for x in log:
            by_surface[x["surface"]].append(x["p"]); by_year[x["date"][:4]].append(x["p"])
        ps = [x["p"] for x in log]
        backtest[tour] = {**summary(ps),
                          "rank_acc": stats["rank_correct"] / max(stats["rank_n"], 1),
                          "by_surface": {s: summary(v) for s, v in sorted(by_surface.items())},
                          "by_year": {y: summary(v) for y, v in sorted(by_year.items())},
                          "calibration": calibration(ps)}

        # --- Elo pro kalkulačku: hráči aktivní za posledních 12 měsíců ---
        latest = max(model.last_date.values())
        year_ago = str(int(latest[:4]) - 1) + latest[4:]
        players = []
        for pid, d in model.last_date.items():
            if d >= year_ago and model.n[pid] >= 3:
                players.append({"id": pid, "name": model.names[pid], "n": model.n[pid], "last": d,
                                "elo": round(model.rating[pid], 1),
                                **{s: round(model.blended(pid, s), 1) for s in elo.SURFACES}})
        players.sort(key=lambda p: -p["elo"])
        ratings_out[tour] = players
        checks[tour] = elo_check(model, tour, year_ago)

        # --- predikce zápasů v rozpisu ---
        for m in upcoming["matches"]:
            if m["tour"] != tour or m["qualifying"] or not m["p1_id"] or not m["p2_id"]:
                continue
            day = local_day(m["date"])
            key = (tour, m["match_id"])
            a = pmap.resolve(m["p1_id"], m["p1_name"])
            b = pmap.resolve(m["p2_id"], m["p2_name"])
            surface = smap.resolve(tour, m["tourney_name"], m["venue"], m["event_start"])
            if m["status"] == "scheduled":
                # dokud se nehraje, predikci přepisujeme nejčerstvějšími ratingy
                new = {
                    "match_id": m["match_id"], "tour": tour, "start": m["date"],
                    "tourney": m["tourney_name"], "round": m["round"], "surface": surface,
                    "p1_id": m["p1_id"], "p1_name": m["p1_name"], "p2_id": m["p2_id"], "p2_name": m["p2_name"],
                    "p1_elo": f"{model.blended(a, surface):.1f}", "p2_elo": f"{model.blended(b, surface):.1f}",
                    "n1": model.n[a], "n2": model.n[b],
                    "p1_prob": f"{model.predict(a, b, surface, int(m['best_of'] or 3)):.4f}",
                    "predicted_at": now.isoformat(timespec="minutes"), "status": "scheduled",
                    "winner": "", "score": "", "model": elo.MODEL_VERSION}
                old = preds.get(key)
                same = old and all(str(old.get(k)) == str(new[k]) for k in new
                                   if k not in ("predicted_at", "start", "status"))
                if same:
                    old["start"] = new["start"]; old["status"] = "scheduled"   # beze změny → čas predikce necháme
                else:
                    preds[key] = new
            elif key in preds:
                # rozehraný / dohraný zápas: predikce zůstává, jak byla před zápasem
                preds[key].update(status=m["status"], winner=m["winner"], score=m["score"])
            if day not in (today, today + timedelta(days=1)):
                continue
            wanted[m["match_id"]] = (a, b, day)
            wanted_meta[m["match_id"]] = ((m["p1_name"], m.get("p1_country", "")), (m["p2_name"], m.get("p2_country", "")))
            pr = preds.get(key)
            p = float(pr["p1_prob"]) if pr else None
            matches_out.append({
                "id": m["match_id"], "tour": tour, "day": day.isoformat(), "start": m["date"],
                "time_valid": m["time_valid"], "tourney": m["tourney_name"], "round": m["round"],
                "surface": surface, "status": m["status"], "score": m["score"],
                "winner": 1 if m["winner"] == m["p1_id"] else 2 if m["winner"] else None,
                "p1": player_info(model, a, m["p1_name"], surface, day),
                "p2": player_info(model, b, m["p2_name"], surface, day),
                "p1_prob": p,
                "fair1": round(1 / p, 2) if p else None, "fair2": round(1 / (1 - p), 2) if p else None,
                "low_data": min(model.n[a], model.n[b]) < LOW_DATA,
                "odds": odds.get(f"{tour}:{m['match_id']}"),
            })

        details_out.update(details(model, tour, wanted, load_players(tour), wanted_meta))
        del model.rows

    # doplnit výsledky i starším predikcím, které už vypadly z okna rozpisu
    for tour in ("atp", "wta"):
        path = os.path.join(elo.DATA_DIR, f"espn_{tour}.csv")
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                pr = preds.get((tour, r["match_id"]))
                if pr and pr["status"] not in ("final", "ret", "wo"):
                    pr.update(status=r["status"], winner=r["winner"], score=r["score"])
    save_predictions(preds)

    # --- živá bilance: jen předem zveřejněné tipy, bez skrečů a kontumací, jen aktuální model ---
    live = {"model": elo.MODEL_VERSION, **elo.MODELS[elo.MODEL_VERSION], "previous": []}
    for v, meta_v in elo.MODELS.items():
        if v == elo.MODEL_VERSION:
            continue
        ps = [(float(p["p1_prob"]) if p["winner"] == p["p1_id"] else 1 - float(p["p1_prob"]))
              for p in preds.values() if p["model"] == v and p["status"] == "final"]
        live["previous"].append({"model": v, **meta_v, **summary(ps)})
    for tour in ("atp", "wta", "all"):
        ps, recent = [], []
        for pr in sorted(preds.values(), key=lambda r: r["start"]):
            if (tour != "all" and pr["tour"] != tour) or pr["status"] != "final" \
                    or pr["model"] != elo.MODEL_VERSION:
                continue
            p1 = float(pr["p1_prob"])
            ps.append(p1 if pr["winner"] == pr["p1_id"] else 1 - p1)
            recent.append({"id": pr["match_id"], "tour": pr["tour"], "start": pr["start"],
                           "tourney": pr["tourney"], "round": pr["round"], "surface": pr["surface"],
                           "p1": pr["p1_name"], "p2": pr["p2_name"], "p1_prob": p1,
                           "winner": 1 if pr["winner"] == pr["p1_id"] else 2, "score": pr["score"]})
        live[tour] = {**summary(ps), "calibration": calibration(ps),
                      "recent": recent[::-1][:100] if tour == "all" else None}
        if tour != "all":
            del live[tour]["recent"]

    os.makedirs(OUT_DIR, exist_ok=True)
    meta = {"generated": now.isoformat(timespec="minutes"), "data_fetched": upcoming["fetched"]}
    matches_out.sort(key=lambda m: (m["day"], m["start"], m["tour"], m["tourney"]))
    for name, payload in (("today.json", {**meta, "matches": matches_out}),
                          ("details.json", {**meta, "details": details_out}),
                          ("ratings.json", {**meta, "scale": elo.CALIBRATION, "long_break": LONG_BREAK,
                                            "low_data": LOW_DATA,
                                            **ratings_out}),
                          ("track.json", {**meta, "backtest": backtest, "live": live, "elo_check": checks,
                                          "odds": odds_track(odds, preds)})):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    n_today = sum(m["day"] == today.isoformat() for m in matches_out)
    print(f"Zápasy: {n_today} dnes, {len(matches_out) - n_today} zítra | "
          f"hráči v kalkulačce: ATP {len(ratings_out['atp'])}, WTA {len(ratings_out['wta'])} | "
          f"živá bilance: {live['all']['n']} vyhodnocených tipů, "
          f"{sum(p['status'] == 'scheduled' for p in preds.values())} čeká")


if __name__ == "__main__":
    main()
