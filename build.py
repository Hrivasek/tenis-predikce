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
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import elo, mapping

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "site", "data")
PRED_PATH = os.path.join(elo.DATA_DIR, "predictions.csv")
PRAGUE = ZoneInfo("Europe/Prague")
LOW_DATA = 10                     # pod tolik zápasů je Elo hráče nespolehlivé
LONG_BREAK = 60                   # dní bez zápasu → štítek „dlouho nehrál“ (model neví o zraněních)
ODDS_PATH = os.path.join(elo.DATA_DIR, "odds.json")     # kurzy zadané z webu (přes GitHub API)
PRED_FIELDS = ["match_id", "tour", "start", "tourney", "round", "surface",
               "p1_id", "p1_name", "p2_id", "p2_name", "p1_elo", "p2_elo", "n1", "n2",
               "p1_prob", "predicted_at", "status", "winner", "score"]


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
    s nejvyšší kladnou hodnotou (EV = p * kurz - 1). Skreč a kontumace = storno."""
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
             "status": pr["status"], "profit": None}
        if e["tip"] and not late and pr["status"] == "final":
            won = pr["winner"] == (pr["p1_id"] if side == 1 else pr["p2_id"])
            e["profit"] = (o["o1"] if side == 1 else o["o2"]) - 1 if won else -1.0
        entries.append(e)
    entries.sort(key=lambda e: e["start"], reverse=True)

    def agg(min_ev):
        done = [e for e in entries if e["profit"] is not None and e["ev"] > min_ev]
        n = len(done)
        profit = sum(e["profit"] for e in done)
        return {"min_ev": min_ev, "n": n, "wins": sum(e["profit"] > 0 for e in done),
                "profit": profit, "roi": profit / n if n else None}
    return {"entries": entries[:200], "total": len(entries),
            "tips_pending": sum(e["tip"] is not None and e["profit"] is None and e["status"] == "scheduled"
                                for e in entries),
            "by_threshold": [agg(t) for t in (0.0, 0.05, 0.10)]}


def load_predictions():
    if not os.path.exists(PRED_PATH):
        return {}
    with open(PRED_PATH, newline="", encoding="utf-8") as f:
        return {(r["tour"], r["match_id"]): r for r in csv.DictReader(f)}


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
    matches_out, ratings_out, backtest = [], {}, {}

    for tour in ("atp", "wta"):
        log = []
        model, stats = elo.run(tour, log=log)
        pmap = mapping.PlayerMap(tour, elo.history(tour))
        smap = mapping.SurfaceMap({t: elo.history(t) for t in ("atp", "wta")})
        # do Elo kvalifikace nepočítáme, ale pro „kdy naposledy hrál“ se hodí
        with open(os.path.join(elo.DATA_DIR, f"espn_{tour}.csv"), newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["qualifying"] == "1" and r["status"] != "wo":
                    d = r["date"][:10].replace("-", "")
                    for i in ("1", "2"):
                        pid = pmap.resolve(r[f"p{i}_id"], r[f"p{i}_name"])
                        if d > model.last_date.get(pid, ""):
                            model.last_date[pid] = d
                            model.names.setdefault(pid, r[f"p{i}_name"])

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
                preds[key] = {
                    "match_id": m["match_id"], "tour": tour, "start": m["date"],
                    "tourney": m["tourney_name"], "round": m["round"], "surface": surface,
                    "p1_id": m["p1_id"], "p1_name": m["p1_name"], "p2_id": m["p2_id"], "p2_name": m["p2_name"],
                    "p1_elo": f"{model.blended(a, surface):.1f}", "p2_elo": f"{model.blended(b, surface):.1f}",
                    "n1": model.n[a], "n2": model.n[b],
                    "p1_prob": f"{model.predict(a, b, surface):.4f}",
                    "predicted_at": now.isoformat(timespec="minutes"), "status": "scheduled",
                    "winner": "", "score": ""}
            elif key in preds:
                # rozehraný / dohraný zápas: predikce zůstává, jak byla před zápasem
                preds[key].update(status=m["status"], winner=m["winner"], score=m["score"])
            if day not in (today, today + timedelta(days=1)):
                continue
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

    # doplnit výsledky i starším predikcím, které už vypadly z okna rozpisu
    for tour in ("atp", "wta"):
        path = os.path.join(elo.DATA_DIR, f"espn_{tour}.csv")
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                pr = preds.get((tour, r["match_id"]))
                if pr and pr["status"] not in ("final", "ret", "wo"):
                    pr.update(status=r["status"], winner=r["winner"], score=r["score"])
    save_predictions(preds)

    # --- živá bilance: jen předem zveřejněné tipy, bez skrečů a kontumací ---
    live = {}
    for tour in ("atp", "wta", "all"):
        ps, recent = [], []
        for pr in sorted(preds.values(), key=lambda r: r["start"]):
            if (tour != "all" and pr["tour"] != tour) or pr["status"] != "final":
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
                          ("ratings.json", {**meta, "scale": elo.CALIBRATION, "long_break": LONG_BREAK,
                                            **ratings_out}),
                          ("track.json", {**meta, "backtest": backtest, "live": live,
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
