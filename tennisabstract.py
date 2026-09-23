#!/usr/bin/env python3
"""
Výsledky challengerů a WTA 125 + Elo žebříčky z Tennis Abstract (Jeff Sackmann, CC BY-NC-SA 4.0).

Běží na Macu (launchd, scripts/ta_sync.sh) – z GitHub Actions web blokuje Cloudflare.
Šetrně: pauza mezi dotazy, jen potřebné stránky, User-Agent s kontaktem na projekt.

  python3 tennisabstract.py                          # běžný běh (2× denně)
  python3 tennisabstract.py --backfill-from 2026-05-26

Zdroje:
  - úvodní stránka TA: seznam právě běžících turnajů (/current/<rok><turnaj>.html)
  - stránky turnajů: dohrané zápasy dvouhry (hráči jsou odkázaní Sackmannovými ID = ID v archivu)
  - kalendář turnajů z Wikipedie (termín, město, země, povrch) – aby šlo dohnat turnaje,
    které proběhly, zatímco Mac neběžel; stránky turnajů obsahují vždy celý turnaj
  - Elo žebříčky TA (jednou týdně) – kontrola našeho modelu
Zapisuje: data/ta/tournaments.json, data/ta_matches.csv, data/ta_elo_{atp,wta}.csv, data/ta/calendar_*.json
"""
import csv, html, json, os, re, sys, time, unicodedata, urllib.error, urllib.request
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
TA_DIR = os.path.join(DATA, "ta")
BASE = "https://www.tennisabstract.com"
UA = "tenis-predikce/1.0 (+https://github.com/Hrivasek/tenis-predikce)"
PAUSE = 5.0                       # sekund mezi dotazy na jeden web
LOOKBACK = 21                     # dny zpět, kdy ještě dohledáváme nedokončené turnaje
HISTORY_END = "2026-05-25"        # turnaje do tohoto týdne už jsou v archivu (import_lower.py)
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], 1)}
WIKI = {"atp": "{y}_ATP_Challenger_Tour", "wta": "{y}_WTA_125_tournaments"}
MATCH_FIELDS = ["slug", "tour", "start", "surface", "round", "winner_id", "winner_name", "winner_ioc",
                "loser_id", "loser_name", "loser_ioc", "score"]
_last = {}


def get(url, host="ta"):
    """GET s pauzou mezi dotazy na stejný web. Vrací (status, text)."""
    wait = PAUSE - (time.time() - _last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, text = r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, ""
    _last[host] = time.time()
    return status, text


def ascii_fold(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def city_key(s):
    return re.sub(r"[^a-z]", "", ascii_fold(s).lower())


# ---------- kalendář (Wikipedie) ----------
def parse_calendar(wikitext, year):
    """Turnaje z tabulek 'Week of | Tournament': týden, název, město, země, povrch."""
    events, week, month = [], None, None
    for line in wikitext.splitlines():
        m = re.match(r"^===\s*(\w+)\s*===", line)
        if m and m.group(1) in MONTHS:
            month = MONTHS[m.group(1)]
        wm = re.search(r"rowspan=\d+\s*\|\s*(" + "|".join(MONTHS) + r")\s+(\d{1,2})\s*\|\|", line)
        if wm:
            week = date(year, MONTHS[wm.group(1)], int(wm.group(2)))
        for tm in re.finditer(r"\[\[\d{4} [^\]|]*\|?([^\]]*)\]\]\s*<br\s*/?>\s*\[\[([^\]|]+)(?:\|([^\]]+))?\]\],\s*([^<]+?)\s*"
                              r"<br\s*/?>\s*(Hard|Clay|Grass|Carpet)", line):
            if week is None:
                continue
            city = (tm.group(3) or tm.group(2)).strip()
            city = re.sub(r"\s*\(.*?\)", "", city)
            surf = "Hard" if tm.group(5) == "Carpet" else tm.group(5)
            events.append({"week": week.isoformat(), "name": tm.group(1).strip(), "city": city,
                           "country": tm.group(4).strip(), "surface": surf})
    return events


def calendar(tour, year, force=False):
    path = os.path.join(TA_DIR, f"calendar_{tour}_{year}.json")
    if os.path.exists(path) and not force:
        with open(path, encoding="utf-8") as f:
            cal = json.load(f)
        if (datetime.now() - datetime.fromisoformat(cal["fetched"])).days < 6:
            return cal["events"]
    page = WIKI[tour].format(y=year)
    status, text = get(f"https://en.wikipedia.org/w/api.php?action=parse&page={page}&prop=wikitext"
                       f"&format=json&formatversion=2", host="wiki")
    if status != 200:
        print(f"  kalendář {page}: HTTP {status}")
        return json.load(open(path, encoding="utf-8"))["events"] if os.path.exists(path) else []
    events = parse_calendar(json.loads(text)["parse"]["wikitext"], year)
    # pořadí turnaje v daném městě (TA: Genoa, Genoa2, ...)
    seen = {}
    for e in sorted(events, key=lambda e: e["week"]):
        k = city_key(e["city"])
        seen[k] = seen.get(k, 0) + 1
        e["nth"] = seen[k]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"fetched": datetime.now().isoformat(timespec="seconds"), "events": events}, f,
                  ensure_ascii=False, indent=0)
    return events


def slug_candidates(tour, e):
    year = e["week"][:4]
    city = ascii_fold(e["city"])
    variants = list(dict.fromkeys([city.replace(" ", ""), city.replace(" ", "").replace("-", ""),
                                   city.split(",")[0].replace(" ", "")]))
    n = str(e["nth"]) if e["nth"] > 1 else ""
    if tour == "atp":
        return [f"{year}{v}{n}Challenger" for v in variants]
    return [f"{year}WTA{v}{n}125" for v in variants]


# ---------- stránky turnajů ----------
def fix_score(score):
    """WTA stránky píšou '64 76(5)' – převedeme na '6-4 7-6(5)'."""
    out = []
    for t in score.split():
        m = re.match(r"^(\d+)(\(\d+\))?$", t)
        if m and "-" not in t:
            d, tb = m.group(1), m.group(2) or ""
            if len(d) == 2:
                t = f"{d[0]}-{d[1]}{tb}"
            elif len(d) == 3:
                t = f"{d[:2]}-{d[2]}{tb}" if d.startswith("1") else f"{d[0]}-{d[1:]}{tb}"
            elif len(d) == 4:
                t = f"{d[:2]}-{d[2:]}{tb}"
        out.append(t)
    return " ".join(out)


def parse_tournament(page):
    """Dohrané zápasy dvouhry: kolo, vítěz a poražený (Sackmann ID, jméno, země), skóre."""
    m = re.search(r"var completedSingles\s*=\s*'(.*?)';", page, flags=re.S)
    if not m:
        return None
    matches = []
    for item in re.split(r"<br\s*/?>", m.group(1)):
        # „d.“ bývá odkazem na vzájemné zápasy: <a href="...&f=...">d.</a>
        item = re.sub(r"<a [^>]*>\s*d\.\s*</a>", " d. ", item)
        rm = re.match(r"\s*([A-Z]+\d*):", item)
        if not rm or " d. " not in item:
            continue
        left, right = item.split(" d. ", 1)
        def player(part):
            pm = re.search(r"w?player\.cgi\?p=(\d+)/[^\"']*[\"']>([^<]+)</a>\s*\(([A-Z]{3})\)", part)
            return (pm.group(1), html.unescape(pm.group(2)).strip(), pm.group(3)) if pm else None
        w, l = player(left), player(right)
        if not w or not l:
            continue
        score = re.sub(r"<[^>]+>", "", right.rsplit(f"({l[2]})", 1)[-1])
        score = fix_score(html.unescape(score).replace("\xa0", " ").strip())
        matches.append({"round": rm.group(1), "winner_id": w[0], "winner_name": w[1], "winner_ioc": w[2],
                        "loser_id": l[0], "loser_name": l[1], "loser_ioc": l[2], "score": score})
    # WTA stránky značí kola R1, R2, … – převedeme na R32, R16, … (podle vzdálenosti od čtvrtfinále)
    nums = [int(x["round"][1:]) for x in matches if re.match(r"^R\d$", x["round"])]
    if nums:
        top = max(nums)
        for x in matches:
            if re.match(r"^R\d$", x["round"]):
                x["round"] = f"R{16 * 2 ** (top - int(x['round'][1:]))}"
    return matches


def current_slugs():
    status, page = get(BASE + "/")
    if status != 200:
        print(f"  úvodní stránka TA: HTTP {status}")
        return []
    slugs = sorted(set(re.findall(r"/current/(\d{4}[A-Za-z0-9-]+?(?:Challenger|125))\.html", page)))
    return slugs


# ---------- registr turnajů a zápasy ----------
def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def load_matches():
    path = os.path.join(DATA, "ta_matches.csv")
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {(r["slug"], r["round"], r["winner_id"], r["loser_id"]): r for r in csv.DictReader(f)}


def save_matches(rows):
    path = os.path.join(DATA, "ta_matches.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MATCH_FIELDS)
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: (r["start"], r["slug"], r["round"], r["winner_id"])))


def monday(d):
    return d - timedelta(days=d.weekday())


def fetch_tournament(slug, meta, matches):
    """slug = klíč v registru; skutečná stránka může být jiná varianta názvu (meta['page'])."""
    status, page = get(f"{BASE}/current/{meta.get('page', slug)}.html")
    if status != 200:
        return status, 0
    ms = parse_tournament(page)
    if ms is None:
        return "bez dat", 0
    for m in ms:
        m.update(slug=slug, tour=meta["tour"], start=meta["start"], surface=meta.get("surface", ""))
        matches[(slug, m["round"], m["winner_id"], m["loser_id"])] = m
    meta["complete"] = any(m["round"] == "F" for m in ms)
    meta["n"] = len(ms)
    meta["fetched"] = datetime.now().isoformat(timespec="seconds")
    return 200, len(ms)


# ---------- Elo žebříčky ----------
def fetch_elo(tour):
    path = os.path.join(DATA, f"ta_elo_{tour}.csv")
    if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < 6 * 86400:
        return None
    status, page = get(f"https://tennisabstract.com/reports/{tour}_elo_ratings.html")
    if status != 200:
        return f"HTTP {status}"
    upd = re.search(r"Last update:\s*(\d{4}-\d{2}-\d{2})", page)
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)]
        pid = re.search(r"player\.cgi\?p=(\d+)", tr)
        # sloupce: pořadí, hráč, věk, Elo, (mezera), pořadí+Elo tvrdý, antuka, tráva, …
        if len(cells) >= 11 and cells[0].isdigit():
            rows.append({"rank": cells[0], "player_id": pid.group(1) if pid else "",
                         "name": cells[1].replace("\xa0", " "),
                         "elo": cells[3], "hard": cells[6], "clay": cells[8], "grass": cells[10],
                         "updated": upd.group(1) if upd else ""})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["rank", "player_id", "name", "elo", "hard", "clay", "grass", "updated"])
        w.writeheader()
        w.writerows(rows)
    return f"{len(rows)} hráčů (aktualizace {upd.group(1) if upd else '?'})"


def main():
    os.makedirs(TA_DIR, exist_ok=True)
    today = date.today()
    since = today - timedelta(days=LOOKBACK)
    if "--backfill-from" in sys.argv:
        since = date.fromisoformat(sys.argv[sys.argv.index("--backfill-from") + 1])
    reg_path = os.path.join(TA_DIR, "tournaments.json")
    reg = load_json(reg_path, {})
    matches = load_matches()
    before = len(matches)

    # kalendář: termíny a povrchy + kandidáti na doběhnutí
    todo = {}
    for tour in ("atp", "wta"):
        for year in sorted({since.year, today.year}):
            for e in calendar(tour, year):
                wk = date.fromisoformat(e["week"])
                if not (since <= wk + timedelta(days=6) and wk <= today) or e["week"] <= HISTORY_END:
                    continue
                key = (tour, e["week"], e["city"])
                todo[key] = e

    # právě běžící turnaje z úvodní stránky TA (přesné názvy stránek)
    for slug in current_slugs():
        tour = "wta" if slug[4:7] == "WTA" else "atp"
        if slug in reg:
            continue
        guess = re.sub(r"\d+$", "", re.sub(r"(Challenger|125)$", "", slug[4:]).replace("WTA", "", 1))
        ev = next((e for (t, w, c), e in todo.items() if t == tour and city_key(c) == city_key(guess)), None)
        reg[slug] = {"tour": tour, "start": ev["week"] if ev else monday(today).isoformat(),
                     "surface": ev["surface"] if ev else "", "city": ev["city"] if ev else guess,
                     "country": ev["country"] if ev else "", "complete": False, "source": "home"}

    # turnaje z kalendáře, které v registru ještě nejsou (proběhly, když Mac neběžel)
    known = {(m["tour"], m["start"], city_key(m.get("city", ""))) for m in reg.values()}
    for (tour, week, city), e in todo.items():
        if (tour, week, city_key(city)) in known:
            continue
        cands = slug_candidates(tour, e)
        reg[cands[0]] = {"tour": tour, "start": week, "surface": e["surface"], "city": e["city"],
                         "country": e["country"], "complete": False, "source": "kalendář", "candidates": cands}

    # stažení: jen nedokončené turnaje z období
    fetched = missing = 0
    for slug, meta in sorted(reg.items(), key=lambda kv: kv[1]["start"]):
        if meta.get("complete") or meta.get("missing") or date.fromisoformat(meta["start"]) + timedelta(days=13) < since:
            continue
        status, n = fetch_tournament(slug, meta, matches)
        for alt in meta.get("candidates", []):
            if status != 404:
                break
            if alt != meta.get("page", slug):
                meta["page"] = alt
                status, n = fetch_tournament(slug, meta, matches)
        if status == 200:
            fetched += 1
            meta.pop("candidates", None)
        elif status == 404 and date.fromisoformat(meta["start"]) < today - timedelta(days=7):
            meta["missing"] = True; missing += 1       # TA stránku nemá – nezkoušet znovu
        print(f"  {meta.get('page', slug):<40} {meta['start']} {meta.get('surface', ''):<5} -> {status} ({n} zápasů)")

    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1, sort_keys=True)
    save_matches(matches)
    for tour in ("atp", "wta"):
        r = fetch_elo(tour)
        if r:
            print(f"  Elo {tour.upper()}: {r}")
    print(f"Tennis Abstract: staženo {fetched} turnajů, nenalezeno {missing}, "
          f"zápasů celkem {len(matches)} (+{len(matches) - before})")


if __name__ == "__main__":
    main()
