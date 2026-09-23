"""
Stahování výsledků a rozpisu z veřejného (neoficiálního) ESPN API.
Jen standardní knihovna. Používají ho update.py a build.py.
"""
import json, re, time, urllib.request
from datetime import datetime, timezone

URL = "https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={dates}"
SINGLES = {"atp": "Men's Singles", "wta": "Women's Singles"}
ROUNDS = {"Round 1": 1, "Round 2": 2, "Round 3": 3, "Round 4": 4, "Round Robin": 5,
          "Quarterfinal": 10, "Semifinal": 11, "Final": 13}
STATUS = {"STATUS_FINAL": "final", "STATUS_RETIRED": "ret", "STATUS_WALKOVER": "wo",
          "STATUS_SCHEDULED": "scheduled", "STATUS_IN_PROGRESS": "live"}


def fetch(tour, dates, retries=3):
    """dates: 'YYYYMMDD' nebo 'YYYYMMDD-YYYYMMDD'."""
    url = URL.format(tour=tour, dates=dates)
    for attempt in range(retries):
        try:
            # pozor: ESPN vrací 403 na některé User-Agenty (i prohlížečové), výchozí urllib projde
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def _athlete(c):
    a = c.get("athlete") or {}
    name = a.get("displayName")
    if not name:
        return None, None
    m = re.search(r"/id/(\d+)", json.dumps(a.get("links", "")))
    return (m.group(1) if m else c.get("id")), name


def _country(c):
    return ((c.get("athlete") or {}).get("flag") or {}).get("alt", "")


def _score(win, lose):
    sets = []
    for a, b in zip(win.get("linescores", []), lose.get("linescores", [])):
        s = f"{int(a.get('value', 0))}-{int(b.get('value', 0))}"
        tb = a.get("tiebreak") if a.get("value", 0) < b.get("value", 0) else b.get("tiebreak")
        if tb is not None and max(a.get("value", 0), b.get("value", 0)) == 7 \
                and min(a.get("value", 0), b.get("value", 0)) == 6:
            s += f"({int(tb)})"
        sets.append(s)
    return " ".join(sets)


def parse(data, tour):
    """Vrátí seznam zápasů dvouhry (dohrané i naplánované) jako dicty."""
    out = []
    for ev in data.get("events", []):
        start = ev.get("date", "")[:10].replace("-", "")
        for g in ev.get("groupings", []):
            if g.get("grouping", {}).get("displayName") != SINGLES[tour]:
                continue
            for c in g.get("competitions", []):
                rnd = (c.get("round") or {}).get("displayName", "")
                status = STATUS.get(c.get("status", {}).get("type", {}).get("name"), "other")
                comps = sorted(c.get("competitors", []), key=lambda x: x.get("order", 0))
                if len(comps) != 2:
                    continue
                (id1, n1), (id2, n2) = _athlete(comps[0]), _athlete(comps[1])
                row = {
                    "match_id": c["id"], "event_id": ev.get("id", ""), "tourney_name": ev.get("name", ""),
                    "venue": (ev.get("venue") or {}).get("displayName", ""), "event_start": start,
                    "tourney_level": "G" if ev.get("major") else "A",
                    "round": rnd, "round_order": ROUNDS.get(rnd, 0),
                    "qualifying": int(rnd.startswith("Qualifying")),
                    "date": c.get("date", ""), "time_valid": int(bool(c.get("timeValid", True))),
                    "status": status,
                    "best_of": (c.get("format") or {}).get("regulation", {}).get("periods", ""),
                    "p1_id": id1 or "", "p1_name": n1 or "", "p2_id": id2 or "", "p2_name": n2 or "",
                    "winner": "", "score": "",
                    "p1_country": _country(comps[0]), "p2_country": _country(comps[1]),
                }
                if status in ("final", "ret", "wo"):
                    wi = 0 if comps[0].get("winner") else 1 if comps[1].get("winner") else None
                    if wi is None or not id1 or not id2:
                        continue
                    row["winner"] = (id1, id2)[wi]
                    row["score"] = _score(comps[wi], comps[1 - wi]) + (" RET" if status == "ret" else "")
                out.append(row)
    return out


def now_utc():
    return datetime.now(timezone.utc)
