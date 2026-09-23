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
PRED_FIELDS = ["match_id", "tour", "start", "tourney", "round", "surface",
               "p1_id", "p1_name", "p2_id", "p2_name", "p1_elo", "p2_elo", "n1", "n2",
               "p1_prob", "predicted_at", "status", "winner", "score"]


def local_day(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PRAGUE).date()


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
    matches_out, ratings_out, backtest = [], {}, {}

    for tour in ("atp", "wta"):
        log = []
        model, stats = elo.run(tour, log=log)
        pmap = mapping.PlayerMap(tour, elo.history(tour))
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
                "p1": {"name": m["p1_name"], "elo": round(model.blended(a, surface)), "n": model.n[a]},
                "p2": {"name": m["p2_name"], "elo": round(model.blended(b, surface)), "n": model.n[b]},
                "p1_prob": p,
                "fair1": round(1 / p, 2) if p else None, "fair2": round(1 / (1 - p), 2) if p else None,
                "low_data": min(model.n[a], model.n[b]) < LOW_DATA,
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
                          ("ratings.json", {**meta, "surface_weight": elo.SURFACE_WEIGHT, **ratings_out}),
                          ("track.json", {**meta, "backtest": backtest, "live": live})):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    n_today = sum(m["day"] == today.isoformat() for m in matches_out)
    print(f"Zápasy: {n_today} dnes, {len(matches_out) - n_today} zítra | "
          f"hráči v kalkulačce: ATP {len(ratings_out['atp'])}, WTA {len(ratings_out['wta'])} | "
          f"živá bilance: {live['all']['n']} vyhodnocených tipů, "
          f"{sum(p['status'] == 'scheduled' for p in preds.values())} čeká")


if __name__ == "__main__":
    main()
