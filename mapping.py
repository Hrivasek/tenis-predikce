"""
Napojení ESPN dat na historická data (Sackmann):
  - hráči: ESPN jméno -> ID hráče z historie (jinak nové ID "E<espn id>")
  - povrch: ESPN neuvádí povrch, odvodíme ho z historie turnajů ve stejném městě
Ruční opravy: data/aliases.json a data/surfaces.json.
"""
import json, os, re, unicodedata
from collections import Counter, defaultdict

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ESPN_ROUNDS = {1: "R1", 2: "R2", 3: "R3", 4: "R4", 5: "RR", 10: "QF", 11: "SF", 13: "F"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).split()


def _load_json(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class PlayerMap:
    """Páruje ESPN hráče na ID z historie. Pořadí pokusů: ruční alias, přesné jméno,
    stejná slova v jiném pořadí (Zhang Zhizhen / Zhizhen Zhang), bez mezer (SoonWoo / Soon Woo),
    první + poslední slovo, a nakonec jedno jméno obsažené v druhém (Irene Burillo /
    Irene Burillo Escorihuela) – volnější pravidla jen při jednoznačné shodě."""

    def __init__(self, tour, hist_rows):
        self.aliases = _load_json("aliases.json").get(tour, {})
        last = {}
        names = {}
        for r in hist_rows:
            for side in ("winner", "loser"):
                pid = r[f"{side}_id"]
                names[pid] = r[f"{side}_name"]
                last[pid] = max(last.get(pid, ""), r["tourney_date"])
        self.exact, self.anyorder, self.joined, self.ends = (defaultdict(list) for _ in range(4))
        self.tokens = {}
        for pid, nm in names.items():
            t = norm(nm)
            if not t:
                continue
            self.tokens[pid] = set(t)
            self.exact[" ".join(t)].append(pid)
            self.anyorder[" ".join(sorted(t))].append(pid)
            self.joined["".join(sorted(t))].append(pid)
            self.joined["".join(t)].append(pid)
            if len(t) >= 2:
                self.ends[t[0] + " " + t[-1]].append(pid)
        self.last = last
        self.names = names
        self.stats = Counter()
        self.unmatched = Counter()
        self.loose = {}                                   # volnější shody – ke kontrole

    def _best(self, pids):
        return max(pids, key=lambda p: self.last.get(p, ""))    # nejaktivnější shoda

    def resolve(self, espn_id, name):
        if espn_id in self.aliases:
            self.stats["alias"] += 1
            return self.aliases[espn_id]
        if name in self.aliases:
            self.stats["alias"] += 1
            return self.aliases[name]
        t = norm(name)
        for kind, key, index in (("exact", " ".join(t), self.exact),
                                 ("anyorder", " ".join(sorted(t)), self.anyorder),
                                 ("joined", "".join(t), self.joined),
                                 ("ends", t[0] + " " + t[-1] if len(t) >= 2 else "", self.ends)):
            pids = set(index.get(key, ()))
            if pids and (kind in ("exact", "anyorder") or len(pids) == 1):
                self.stats[kind] += 1
                if kind != "exact":
                    self.loose[name] = (kind, self.names[self._best(pids)])
                return self._best(pids)
        if len(t) >= 2:
            ts = set(t)
            pids = [p for p, pt in self.tokens.items()
                    if len(pt) >= 2 and (ts < pt or pt < ts) and t[0] in pt]
            if pids and len({frozenset(self.tokens[p]) for p in pids}) == 1:   # i duplicitní ID téhož jména
                self.stats["subset"] += 1
                self.loose[name] = ("subset", self.names[self._best(pids)])
                return self._best(pids)
        self.stats["new"] += 1
        self.unmatched[name] += 1
        return "E" + espn_id


class SurfaceMap:
    """Povrch podle historie: turnaj ve stejném městě (nebo se stejným názvem) a podobném měsíci."""

    def __init__(self, hist_rows_by_tour):
        self.hist = defaultdict(Counter)                 # název -> Counter((tour, měsíc, povrch))
        for tour, rows in hist_rows_by_tour.items():
            for r in rows:
                if r["tourney_date"] >= "2018" and r["_surface"]:
                    key = " ".join(norm(r["tourney_name"]))
                    self.hist[key][(tour, int(r["tourney_date"][4:6]), r["_surface"])] += 1
        self.rules = _load_json("surfaces.json").get("rules", [])
        self.unknown = Counter()

    def resolve(self, tour, tourney_name, venue, start):
        month = int(start[4:6])
        text = " ".join(norm(tourney_name + " " + venue))
        for rule in self.rules:                          # ruční pravidla mají přednost
            if " ".join(norm(rule["match"])) in text and month in rule.get("months", range(1, 13)):
                return rule["surface"]
        city = " ".join(norm(venue.split(",")[0]))
        cands = [k for k in self.hist if k == city or (len(k) > 3 and f" {k} " in f" {text} ")]
        votes = Counter()
        for k in cands:
            for (t, m, s), n in self.hist[k].items():
                near = min(abs(m - month), 12 - abs(m - month)) <= 1
                votes[s] += n * (2 if t == tour else 1) * (10 if near else 1)
        if votes:
            return votes.most_common(1)[0][0]
        self.unknown[(tourney_name, venue)] += 1
        return "Hard"                                    # většina neznámých turnajů je na betonu


def espn_rows(tour, espn_results, pmap, smap, cutoff, quals=False):
    """Převede ESPN výsledky na řádky ve formátu historických dat.
    cutoff: turnaje začínající nejpozději tímto dnem už jsou v historii – přeskočit."""
    out = []
    for r in espn_results:
        if r["event_start"] <= cutoff or r["status"] == "wo":
            continue
        if r["qualifying"] == "1" and not quals:
            continue
        w = 1 if r["winner"] == r["p1_id"] else 2
        l = 3 - w
        wid = pmap.resolve(r[f"p{w}_id"], r[f"p{w}_name"])
        lid = pmap.resolve(r[f"p{l}_id"], r[f"p{l}_name"])
        surface = smap.resolve(tour, r["tourney_name"], r["venue"], r["event_start"])
        rnd = ESPN_ROUNDS.get(int(r["round_order"] or 0), "Q")
        out.append({
            "tourney_id": "espn-" + r["event_id"], "tourney_name": r["tourney_name"],
            "surface": surface, "_surface": surface, "tourney_level": r["tourney_level"],
            "tourney_date": r["event_start"], "match_num": r["match_id"], "round": rnd,
            "winner_id": wid, "winner_name": r[f"p{w}_name"], "loser_id": lid, "loser_name": r[f"p{l}_name"],
            "score": r["score"], "best_of": r["best_of"], "winner_rank": "", "loser_rank": "",
            "_ret": r["status"] == "ret", "_date": r["date"], "_source": "espn",
        })
    return out
