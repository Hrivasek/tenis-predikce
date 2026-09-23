#!/usr/bin/env python3
"""
Tenisový Elo model (ATP / WTA) – verze 2.
Běží jen se standardní knihovnou Pythonu (nic není potřeba instalovat).

Použití:
  python3 elo.py test                      # změří přesnost na zápasech 2023+ (historie + ESPN)
  python3 elo.py top atp Clay              # top 20 hráčů podle Elo (povrch: Hard/Clay/Grass, nebo "all")
  python3 elo.py predict atp Hard "Jannik Sinner" "Carlos Alcaraz"
"""
import csv, gzip, math, os, re, sys
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ROUND_ORDER = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3, "ER": 4, "RR": 5, "R128": 6, "R64": 7,
               "R32": 8, "R16": 9, "QF": 10, "SF": 11, "BR": 12, "F": 13}
SURFACES = ("Hard", "Clay", "Grass")

# Parametry modelu (naladěné na zápasech 2010–2022, testované na 2023+)
K_BASE, K_OFFSET, K_SHAPE = 300.0, 5.0, 0.4   # K = 300 / (počet_zápasů + 5)^0.4
SLAM_MULT = 1.1                                # grandslamy mají o něco větší váhu
SURFACE_WEIGHT = 0.5                           # predikce = mix celkového a povrchového Elo
# Elo podle skóre: K se násobí podle podílu vyhraných gemů (0,62 = běžná výhra = beze změny,
# kanár ~2×, těsná výhra ~0,4×). Síla efektu naladěná zvlášť pro okruhy.
MOV = {"atp": 0.45, "wta": 0.6}
# Kalibrace: čisté Elo je u favoritů přehnaně sebejisté. Rozdíl ratingů při predikci násobíme
# koeficientem < 1 (naladěno na 2010–2022 minimalizací log-loss, ověřeno na 2023+).
# Aktualizace ratingů zůstávají nekalibrované.
CALIBRATION = {"atp": 0.85, "wta": 0.776}
START = 1500.0
# Zápasy nižších úrovní (challengery, kvalifikace, ITF/Futures, WTA 125) mají v Elo plnou váhu –
# nižší váhy (0,25–0,75) testované na 2010–2022 vycházely hůř nebo stejně.


def expected(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def best_of_5(p):
    """Pravděpodobnost výhry na 2 vítězné sety -> na 3 vítězné (sety jako nezávislé pokusy)."""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        s = (lo + hi) / 2
        if s * s * (3 - 2 * s) < p:
            lo = s
        else:
            hi = s
    s = (lo + hi) / 2
    return s ** 3 * (10 - 15 * s + 6 * s * s)


def game_share(score):
    """Podíl gemů vítěze ze skóre typu '7-6(4) 3-6 6-2'; None, když skóre chybí."""
    gw = gl = 0
    for tok in (score or "").split():
        m = re.match(r"^(\d+)-(\d+)", tok)
        if m:
            gw += int(m.group(1)); gl += int(m.group(2))
    return gw / (gw + gl) if gw + gl else None


def load_history(tour):
    """Historická data (Sackmann, do 25. 5. 2026)."""
    path = os.path.join(DATA_DIR, f"{tour}_all.csv")
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            score = r.get("score") or ""
            if not r["winner_id"] or not r["loser_id"]:
                continue
            if "W/O" in score or "DEF" in score or "Walkover" in score:
                continue                      # nehrálo se – nic z toho nevyčteme
            r["_ret"] = "RET" in score
            surf = (r["surface"] or "").capitalize()
            r["_surface"] = "Hard" if surf == "Carpet" else surf
            r["_key"] = (r["tourney_date"], r["tourney_id"], ROUND_ORDER.get(r["round"], 5),
                         int(r["match_num"] or 0))
            r["_eval"] = True                 # hlavní soutěž okruhu – hodnotí se v testu
            rows.append(r)
    return rows


def load_lower(tour):
    """Nižší úrovně (import_lower.py): challengery, kvalifikace, ITF/Futures, WTA 125."""
    path = os.path.join(DATA_DIR, f"lower_{tour}.csv.gz")
    rows = []
    if not os.path.exists(path):
        return rows
    with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            score = r["score"] or ""
            if not r["winner_id"] or not r["loser_id"] or "W/O" in score or "DEF" in score or "Walkover" in score:
                continue
            surf = (r["surface"] or "").capitalize()
            r["_surface"] = "Hard" if surf == "Carpet" else surf
            r["_ret"] = "RET" in score
            r["_key"] = (r["tourney_date"], "L" + r["tourney_id"], ROUND_ORDER.get(r["round"], 5),
                         int(r["match_num"] or 0))
            r["_eval"] = False
            r["tourney_level"] = "L"          # grandslamové kvalifikace nemají násobek grandslamu
            rows.append(r)
    return rows


_HISTORY = {}
PLAYER_MAPS = {}      # PlayerMap použitá při posledním load_matches – build.py s ní páruje rozpis


def history(tour):
    if tour not in _HISTORY:
        _HISTORY[tour] = load_history(tour)
    return _HISTORY[tour]


def load_espn(tour, quals=False, lower=None):
    """Čerstvá data z ESPN (update.py) převedená na formát historie. Vrací (řádky, PlayerMap, SurfaceMap).
    lower: řádky nižších úrovní – jejich hráči se použijí i pro párování jmen."""
    import mapping
    path = os.path.join(DATA_DIR, f"espn_{tour}.csv")
    hist = history(tour)
    pmap = mapping.PlayerMap(tour, hist + (lower or []))
    smap = mapping.SurfaceMap({t: history(t) for t in ("atp", "wta")})
    if not os.path.exists(path):
        return [], pmap, smap
    with open(path, newline="", encoding="utf-8") as f:
        raw = list(csv.DictReader(f))
    cutoff = max(r["tourney_date"] for r in hist)
    rows = mapping.espn_rows(tour, raw, pmap, smap, cutoff, quals=quals)
    for r in rows:
        r["_key"] = (r["tourney_date"], r["tourney_id"], 0 if r["round"] == "Q" else 1, r["_date"])
        r["_eval"] = r["round"] != "Q"
    return rows, pmap, smap


def load_matches(tour, espn=True, lower=True, quals=None):
    """Všechny zápasy chronologicky. S nižšími úrovněmi bereme z ESPN i kvalifikace (v historii jsou taky)."""
    low = load_lower(tour) if lower else []
    rows = list(history(tour)) + low
    if espn:
        esp, PLAYER_MAPS[tour], _ = load_espn(tour, bool(low) if quals is None else quals, low)
        rows += esp
    rows.sort(key=lambda r: r["_key"])       # chronologicky – žádný únik dat z budoucnosti
    return rows


class Elo:
    def __init__(self, k_base=K_BASE, k_offset=K_OFFSET, k_shape=K_SHAPE,
                 slam_mult=SLAM_MULT, surface_weight=SURFACE_WEIGHT, calibration=1.0, mov=0.0):
        self.kb, self.ko, self.ks = k_base, k_offset, k_shape
        self.slam, self.sw = slam_mult, surface_weight
        self.cal, self.mov = calibration, mov
        self.rating = defaultdict(lambda: START)
        self.n = defaultdict(int)           # zápasy všech úrovní
        self.tn = defaultdict(int)          # jen hlavní soutěž okruhu (pro stejnou testovací sadu jako dřív)
        self.srating = {s: defaultdict(lambda: START) for s in SURFACES}
        self.sn = {s: defaultdict(int) for s in SURFACES}
        self.names = {}
        self.last_date = {}

    def k(self, n):
        return self.kb / ((n + self.ko) ** self.ks)

    def blended(self, pid, surface):
        r = self.rating[pid]
        if surface in SURFACES:
            return (1 - self.sw) * r + self.sw * self.srating[surface][pid]
        return r

    def predict(self, a, b, surface, best_of=3):
        diff = (self.blended(a, surface) - self.blended(b, surface)) * self.cal
        p = expected(diff, 0.0)
        return best_of_5(p) if best_of == 5 else p

    def update(self, w, l, surface, slam=False, best_of=3, score=""):
        mult = self.slam if slam else 1.0
        gs = game_share(score) if self.mov else None
        if gs is not None:
            mult *= min(3.0, max(0.1, 1 + self.mov * ((gs - 0.5) / 0.12 - 1)))
        exp_ = best_of_5 if best_of == 5 else (lambda p: p)
        e = exp_(expected(self.rating[w], self.rating[l]))
        kw, kl = self.k(self.n[w]) * mult, self.k(self.n[l]) * mult
        self.rating[w] += kw * (1 - e)
        self.rating[l] -= kl * (1 - e)
        self.n[w] += 1; self.n[l] += 1
        if surface in SURFACES:
            sr, sn = self.srating[surface], self.sn[surface]
            e = exp_(expected(sr[w], sr[l]))
            sr[w] += self.k(sn[w]) * mult * (1 - e)
            sr[l] -= self.k(sn[l]) * mult * (1 - e)
            sn[w] += 1; sn[l] += 1


def run(tour, eval_from="20230101", min_matches=10, espn=True, lower=True, freeze_after=None,
        eval_to="99999999", log=None, **params):
    """Projede všechny zápasy. Před každým zápasem udělá predikci (tu hodnotíme),
    teprve potom rating aktualizuje. freeze_after: od toho data už rating neaktualizuje
    (simulace modelu, který nedostává čerstvá data). log: seznam, kam se uloží každá
    hodnocená predikce (pro podrobné statistiky). Hodnotí se jen hlavní soutěž okruhu, kde oba
    hráči mají aspoň min_matches zápasů hlavní soutěže – stejná sada jako před přidáním challengerů."""
    params.setdefault("calibration", CALIBRATION.get(tour, 1.0))
    params.setdefault("mov", MOV.get(tour, 0.0))
    elo = Elo(**params)
    stats = defaultdict(float)
    for r in load_matches(tour, espn=espn, lower=lower):
        w, l, surf = r["winner_id"], r["loser_id"], r["_surface"]
        elo.names[w], elo.names[l] = r["winner_name"], r["loser_name"]
        # datum posledního zápasu: u ESPN přesné, u historie jen začátek turnaje
        elo.last_date[w] = elo.last_date[l] = r["_date"][:10].replace("-", "") if r.get("_date") else r["tourney_date"]
        bo = int(r.get("best_of") or 3)
        if (r["_eval"] and eval_from <= r["tourney_date"] <= eval_to and not r["_ret"]
                and elo.tn[w] >= min_matches and elo.tn[l] >= min_matches):
            p = elo.predict(w, l, surf, bo)           # pravděpodobnost, že vyhraje skutečný vítěz
            stats["n"] += 1
            stats["correct"] += p > 0.5
            stats["logloss"] += -math.log(max(p, 1e-9))
            stats["brier"] += (1 - p) ** 2
            if log is not None:
                log.append({"date": r["tourney_date"], "surface": surf, "p": p, "nmin": min(elo.n[w], elo.n[l]), "win_less_exp": elo.n[w] < elo.n[l]})
            try:                                       # srovnání: vyhraje lépe postavený v žebříčku
                wr, lr = int(r["winner_rank"]), int(r["loser_rank"])
                stats["rank_n"] += 1
                stats["rank_correct"] += wr < lr
            except (ValueError, KeyError):
                pass
        if r["_eval"]:
            elo.tn[w] += 1; elo.tn[l] += 1
        if not r["_ret"] and (freeze_after is None or r["tourney_date"] <= freeze_after):
            elo.update(w, l, surf, slam=r["tourney_level"] == "G", best_of=bo, score=r["score"])
    return elo, stats


def report(tour, stats):
    n = stats["n"]
    print(f"{tour.upper()}: {int(n)} zápasů | Elo trefa {stats['correct']/n:.1%} | "
          f"log-loss {stats['logloss']/n:.3f} | Brier {stats['brier']/n:.3f} | "
          f"žebříček trefa {stats['rank_correct']/max(stats['rank_n'],1):.1%}")


def find_player(elo, name):
    name_l = name.lower()
    hits = [pid for pid, nm in elo.names.items() if nm and name_l in nm.lower()]
    if not hits:
        sys.exit(f"Hráč '{name}' nenalezen.")
    return max(hits, key=lambda p: elo.last_date.get(p, ""))   # nejaktivnější shoda


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    if cmd == "test":
        for tour in ("atp", "wta"):
            _, st = run(tour)
            report(tour, st)
    elif cmd == "top":
        tour, surf = sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "all"
        elo, _ = run(tour)
        latest = max(elo.last_date.values())
        active = [p for p, d in elo.last_date.items() if d >= str(int(latest[:4]) - 1) + latest[4:]
                  and elo.n[p] >= 20]
        active.sort(key=lambda p: -elo.blended(p, surf))
        print(f"Top 20 {tour.upper()} ({surf}), data do {latest}:")
        for i, p in enumerate(active[:20], 1):
            print(f"{i:2}. {elo.names[p]:<28} {elo.blended(p, surf):7.0f}")
    elif cmd == "predict":
        tour, surf, a, b = sys.argv[2:6]
        elo, _ = run(tour)
        pa, pb = find_player(elo, a), find_player(elo, b)
        p = elo.predict(pa, pb, surf)
        print(f"{elo.names[pa]} ({elo.blended(pa, surf):.0f}) vs {elo.names[pb]} "
              f"({elo.blended(pb, surf):.0f}) na povrchu {surf}")
        print(f"  {elo.names[pa]}: {p:.1%}   |   {elo.names[pb]}: {1-p:.1%}")
        print(f"  férový kurz: {1/p:.2f} vs {1/(1-p):.2f}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
