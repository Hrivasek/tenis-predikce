#!/usr/bin/env python3
"""
Stáhne čerstvé výsledky a rozpis z ESPN.

  python3 update.py              # posledních 10 dní + rozpis na 3 dny dopředu
  python3 update.py --backfill   # vše od konce historických dat (Sackmann, 25. 5. 2026)

Zapisuje:
  data/espn_atp.csv, data/espn_wta.csv   dohrané zápasy dvouhry (i kvalifikace), bez duplicit
  data/upcoming.json                     všechny zápasy dvouhry v okně včera..+3 dny
"""
import csv, json, os, sys
from datetime import date, timedelta

import espn

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BACKFILL_FROM = date(2026, 5, 26)          # den po posledním turnaji v historických datech
FIELDS = ["match_id", "event_id", "tourney_name", "venue", "event_start", "tourney_level",
          "round", "round_order", "qualifying", "date", "status", "best_of",
          "p1_id", "p1_name", "p2_id", "p2_name", "winner", "score"]


def days(start, end):
    # po jednotlivých dnech: rozsahy "A-B" vrací ESPN nespolehlivě (někdy prázdné)
    while start <= end:
        yield start
        start += timedelta(days=1)


def results_path(tour):
    return os.path.join(DATA_DIR, f"espn_{tour}.csv")


def load_results(tour):
    path = results_path(tour)
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["match_id"]: r for r in csv.DictReader(f)}


def save_results(tour, rows):
    ordered = sorted(rows.values(), key=lambda r: (r["date"], r["match_id"]))
    with open(results_path(tour), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(ordered)


def main():
    today = espn.now_utc().date()
    backfill = "--backfill" in sys.argv
    upcoming = []
    for tour in ("atp", "wta"):
        stored = load_results(tour)
        if backfill or not stored:
            start = BACKFILL_FROM
        else:
            start = date.fromisoformat(max(r["date"] for r in stored.values())[:10]) - timedelta(days=10)
        before = len(stored)
        for day in days(start, today + timedelta(days=3)):
            for r in espn.parse(espn.fetch(tour, day.strftime("%Y%m%d")), tour):
                if r["status"] in ("final", "ret", "wo"):
                    stored[r["match_id"]] = r
                if day >= today - timedelta(days=1):
                    r["tour"] = tour
                    upcoming.append(r)
        save_results(tour, stored)
        print(f"{tour.upper()}: {len(stored)} dohraných zápasů (+{len(stored) - before})")

    # jeden zápas se může objevit ve víc turnajových dnech – necháme poslední verzi
    uniq = {(r["tour"], r["match_id"]): r for r in upcoming}
    with open(os.path.join(DATA_DIR, "upcoming.json"), "w", encoding="utf-8") as f:
        json.dump({"fetched": espn.now_utc().isoformat(timespec="seconds"),
                   "matches": sorted(uniq.values(), key=lambda r: r["date"])},
                  f, ensure_ascii=False, indent=0)
    print(f"Rozpis: {len(uniq)} zápasů v okně {today - timedelta(days=1)} .. {today + timedelta(days=3)}")


if __name__ == "__main__":
    main()
