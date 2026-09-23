#!/usr/bin/env python3
"""
Jednorázový import zápasů nižších úrovní (challengery, kvalifikace, ITF/Futures, WTA 125)
z archivu Sackmannových dat do kompaktních souborů data/lower_atp.csv.gz a data/lower_wta.csv.gz.

  python3 import_lower.py

Zdroj: https://github.com/Aneeshers/tennis-sackmann-archive (CC BY-NC-SA 4.0), data do 1. 6. 2026.
Bereme jen turnaje začínající nejpozději posledním dnem hlavní historie – novější zápasy
tour kvalifikací přicházejí z ESPN, aby se nic nezapočítalo dvakrát.
"""
import csv, gzip, io, os, urllib.request

import elo

BASE = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main"
FILES = {"atp": ["atp/atp_matches_qual_chall_{y}", "atp/atp_matches_futures_{y}"],
         "wta": ["wta/wta_matches_qual_itf_{y}"]}
YEARS = range(2000, 2027)
KEEP = ["tourney_id", "tourney_name", "surface", "tourney_level", "tourney_date", "match_num",
        "round", "best_of", "winner_id", "winner_name", "loser_id", "loser_name", "score"]
LOCAL = os.path.join(elo.DATA_DIR, "lower")        # volitelná lokální kopie (data/lower/*.csv)


def read(path):
    local = os.path.join(LOCAL, os.path.basename(path) + ".csv")
    if os.path.exists(local):
        with open(local, encoding="utf-8", errors="replace") as f:
            return f.read()
    try:
        with urllib.request.urlopen(f"{BASE}/{path}.csv", timeout=120) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""
        raise


def main():
    for tour, patterns in FILES.items():
        cutoff = max(r["tourney_date"] for r in elo.history(tour))
        rows = []
        for y in YEARS:
            for p in patterns:
                text = read(p.format(y=y))
                for r in csv.DictReader(io.StringIO(text)):
                    if r.get("tourney_date", "") <= cutoff:
                        rows.append({k: r.get(k, "") for k in KEEP})
        rows.sort(key=lambda r: (r["tourney_date"], r["tourney_id"]))
        out = os.path.join(elo.DATA_DIR, f"lower_{tour}.csv.gz")
        with gzip.open(out, "wt", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=KEEP)
            w.writeheader()
            w.writerows(rows)
        print(f"{tour.upper()}: {len(rows)} zápasů do {cutoff} -> {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
