"use strict";
/* Tenis Elo – statický web. Data generuje build.py do data/*.json. */

const TZ = "Europe/Prague";
const SURF = { Hard: "Tvrdý", Clay: "Antuka", Grass: "Tráva" };
const ROUND = { "Round 1": "1. kolo", "Round 2": "2. kolo", "Round 3": "3. kolo", "Round 4": "4. kolo",
  "Round Robin": "Skupina", "Quarterfinal": "Čtvrtfinále", "Semifinal": "Semifinále", "Final": "Finále" };
const S = { today: null, ratings: null, track: null, day: null, tour: "all", calcTour: "atp", calcSurface: "Hard" };

/* ---------- pomocné ---------- */
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (p, d = 0) => (p * 100).toFixed(d).replace(".", ",") + " %";
const num = (x, d = 2) => Number(x).toFixed(d).replace(".", ",");
const signPct = (x) => (x > 0 ? "+" : "") + (x * 100).toFixed(1).replace(".", ",") + " %";
const parseOdd = (v) => { const x = parseFloat(String(v).replace(",", ".")); return x > 1 ? x : null; };
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* soukromé okno apod. */ } },
  del(k) { try { localStorage.removeItem(k); } catch { } },
};
function toast(msg, ms = 2600) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), ms);
}
function timeOf(iso) {
  return new Date(iso).toLocaleTimeString("cs-CZ", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
}
function dayLabel(isoDay) {
  const d = new Date(isoDay + "T12:00:00");
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: TZ });
  const w = d.toLocaleDateString("cs-CZ", { weekday: "short", day: "numeric", month: "numeric" });
  if (isoDay === today) return "Dnes · " + w;
  const tm = new Date(Date.now() + 864e5).toLocaleDateString("sv-SE", { timeZone: TZ });
  if (isoDay === tm) return "Zítra · " + w;
  return w;
}
function seg(el, value, onChange) {
  el.querySelectorAll("button").forEach((b) => {
    b.classList.toggle("on", b.dataset.v === value);
    b.onclick = () => { el.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); onChange(b.dataset.v); };
  });
}
function breakFlag(p) {
  return p.long_break ? `<span class="chip warn" title="Model neví o zraněních ani o Challengerech">⏸ ${esc(p.name.split(" ").slice(-1)[0])}: ${p.days_off} dní bez zápasu na okruhu</span>` : "";
}

/* ---------- GitHub: ukládání kurzů ---------- */
function repoName() {
  const saved = store.get("repo", "");
  if (saved) return saved;
  if (location.hostname.endsWith(".github.io")) {
    const owner = location.hostname.split(".")[0];
    const repo = location.pathname.split("/").filter(Boolean)[0];
    if (repo) return owner + "/" + repo;
  }
  return "Hrivasek/tenis-predikce";
}
const b64enc = (s) => btoa(String.fromCharCode(...new TextEncoder().encode(s)));
const b64dec = (s) => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\n/g, "")), (c) => c.charCodeAt(0)));

async function gh(path, opts = {}) {
  const token = store.get("token", "");
  if (!token) throw new Error("Chybí GitHub token (ozubené kolo vpravo nahoře).");
  const r = await fetch(`https://api.github.com/repos/${repoName()}${path}`, {
    ...opts, cache: "no-store",
    headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}`, ...(opts.headers || {}) },
  });
  if (!r.ok) {
    const e = new Error(r.status === 401 ? "Token je neplatný." : r.status === 404 ? "Repozitář nenalezen nebo token nemá přístup." : `GitHub ${r.status}`);
    e.status = r.status; throw e;
  }
  return r.status === 204 ? null : r.json();
}

async function saveOddsRemote(key, entry) {
  for (let attempt = 0; attempt < 3; attempt++) {
    let sha, data = {};
    try {
      const f = await gh("/contents/data/odds.json?ref=main");
      sha = f.sha; data = JSON.parse(b64dec(f.content) || "{}");
    } catch (e) { if (e.status !== 404) throw e; }
    if (entry) data[key] = entry; else delete data[key];
    const sorted = Object.fromEntries(Object.entries(data).sort());
    try {
      await gh("/contents/data/odds.json", {
        method: "PUT",
        body: JSON.stringify({ message: `Kurz ${key}${entry ? ` ${entry.o1}/${entry.o2}` : " smazán"}`,
          content: b64enc(JSON.stringify(sorted, null, 1) + "\n"), sha, branch: "main" }),
      });
      return;
    } catch (e) { if (e.status !== 409 && e.status !== 422) throw e; }   // mezitím někdo commitnul → znovu
  }
  throw new Error("Soubor se mezitím změnil, zkus to znovu.");
}

/* lokální kopie zadaných kurzů, aby byly vidět hned (web se přegeneruje až po běhu Actions) */
const localOdds = () => store.get("odds-local", {});
function oddsFor(m) {
  const key = `${m.tour}:${m.id}`;
  return localOdds()[key] || m.odds || null;
}

/* ---------- ZÁPASY ---------- */
/* unreliable: aspoň jeden hráč má málo dat → kladnou hodnotu ukážeme šedě s varováním */
function evBlock(p1, o1, o2, n1, n2, unreliable = false) {
  const e1 = p1 * o1 - 1, e2 = (1 - p1) * o2 - 1;
  const cell = (n, e, o, p) => `<div><div class="muted">${esc(n)} @ ${num(o)}</div>
    <b class="${e > 0 && !unreliable ? "pos" : "neg"}">${e > 0 ? "hodnota " : ""}${signPct(e)}</b>
    ${e > 0 && unreliable ? `<div class="unrel">nespolehlivé – málo dat</div>` : ""}
    <div class="muted">férový ${num(1 / p)}</div></div>`;
  const margin = 1 / o1 + 1 / o2 - 1;
  return `<div class="ev">${cell(n1, e1, o1, p1)}${cell(n2, e2, o2, 1 - p1)}</div>
    <div class="muted" style="margin-top:4px">Marže sázkovky ${pct(margin, 1)}</div>`;
}

function matchCard(m) {
  const p = m.p1_prob;
  const done = m.status === "final" || m.status === "ret" || m.status === "wo";
  const cls = (i) => (done && m.winner ? (m.winner === i ? "won" : "lost") : "");
  const when = m.status === "live" ? '<span class="chip live">živě</span>'
    : done ? "" : m.time_valid ? timeOf(m.start) : "čas upřesní";
  const row = (i, pl) => `<div class="pl p${i} ${cls(i)}">
      <span class="name">${esc(pl.name)}<small class="num">${pl.elo}</small></span>
      ${p != null ? `<span class="prob num">${i === 1 ? Math.round(p * 100) : 100 - Math.round(p * 100)} %</span>
      <span class="fair num">${num(i === 1 ? m.fair1 : m.fair2)}</span>` : "<span></span><span></span>"}</div>`;
  let verdict = "";
  if (done && m.winner && p != null && m.status === "final") {
    const hit = (p >= 0.5 ? 1 : 2) === m.winner;
    verdict = `<span class="verdict ${hit ? "ok" : "ko"}">${hit ? "model trefil" : "model netrefil"}</span>`;
  }
  const flags = [
    m.low_data ? `<span class="chip info" title="Pod ${S.ratings.low_data} zápasy je Elo nespolehlivé">málo dat (${Math.min(m.p1.n, m.p2.n)} záp.)</span>` : "",
    breakFlag(m.p1), breakFlag(m.p2),
    p == null ? `<span class="chip">${done ? "bez předzápasové predikce" : "bez predikce"}</span>` : "",
  ].join("");
  const odds = oddsFor(m);
  let oddsHtml = "";
  if (p != null && odds) {
    oddsHtml = `<div class="odds-saved">Kurzy${odds.book ? " " + esc(odds.book) : ""}: ${evBlock(p, odds.o1, odds.o2, m.p1.name.split(" ").slice(-1)[0], m.p2.name.split(" ").slice(-1)[0], m.low_data)}</div>`;
  }
  const canEnter = p != null && m.status === "scheduled";
  return `<article class="card match" data-key="${m.tour}:${m.id}">
    <div class="meta"><span>${ROUND[m.round] || esc(m.round)} · ${m.tour.toUpperCase()}</span><span class="right">${when}</span></div>
    ${row(1, m.p1)}
    ${p != null ? `<div class="bar" aria-hidden="true"><i style="width:${(p * 100).toFixed(1)}%"></i></div>` : ""}
    ${row(2, m.p2)}
    ${done ? `<div class="score">${m.status === "wo" ? "kontumace" : esc(m.score)} ${verdict}</div>` : ""}
    ${flags.trim() ? `<div class="flags">${flags}</div>` : ""}
    ${oddsHtml}
    <div class="card-actions">
      <button class="detail-toggle" aria-expanded="false">Detail zápasu</button>
      ${canEnter ? `<button class="odds-toggle">${odds ? "Upravit kurzy" : "+ Zadat kurzy"}</button>` : ""}
    </div>
  </article>`;
}

function renderMatches() {
  const box = $("#matches");
  const all = S.today.matches;
  const days = [...new Set(all.map((m) => m.day))].sort();
  if (!S.day || !days.includes(S.day)) S.day = days[0];
  const daySeg = $("#day-seg");
  daySeg.innerHTML = days.map((d) => `<button data-v="${d}">${dayLabel(d)}</button>`).join("");
  seg(daySeg, S.day, (v) => { S.day = v; renderMatches(); });
  seg($("#tour-seg"), S.tour, (v) => { S.tour = v; store.set("tour", v); renderMatches(); });

  const list = all.filter((m) => m.day === S.day && (S.tour === "all" || m.tour === S.tour));
  if (!list.length) { box.innerHTML = `<div class="empty">Na tento den nejsou v rozpisu žádné zápasy hlavní soutěže.</div>`; return; }
  const groups = new Map();
  for (const m of list) {
    const k = m.tour + "|" + m.tourney;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(m);
  }
  box.innerHTML = [...groups.values()].map((ms) => `
    <div class="tourney"><h3>${esc(ms[0].tourney)}</h3>
      <span class="chip">${ms[0].tour.toUpperCase()}</span><span class="chip ${ms[0].surface}">${SURF[ms[0].surface] || ms[0].surface}</span></div>
    ${ms.map(matchCard).join("")}`).join("");
  box.querySelectorAll(".odds-toggle").forEach((b) => b.addEventListener("click", () => openOddsForm(b.closest(".match"))));
  box.querySelectorAll(".detail-toggle").forEach((b) => b.addEventListener("click", () => toggleDetail(b)));
}

/* ---------- DETAIL ZÁPASU ---------- */
let detailsPromise = null;
function loadDetails() {
  if (!detailsPromise) detailsPromise = load("details").then((d) => d.details).catch(() => { detailsPromise = null; return null; });
  return detailsPromise;
}
const fmtDay = (iso, exact) => {
  const [y, m, d] = iso.split("-");
  return exact ? `${+d}. ${+m}. ${y}` : `týden ${+d}. ${+m}. ${y}`;
};
const RND = { R1: "1. kolo", R2: "2. kolo", R3: "3. kolo", R4: "4. kolo", R128: "1/64", R64: "1/32", R32: "1/16", R16: "osmifinále",
  QF: "čtvrtfinále", SF: "semifinále", F: "finále", RR: "skupina", Q: "kval.", Q1: "kval.", Q2: "kval.", Q3: "kval.", Q4: "kval.", BR: "o 3. místo" };
const surfChip = (s) => (SURF[s] ? `<span class="chip ${s}">${SURF[s]}</span>` : "");
function matchLine(m, head, tail) {
  return `<div class="dl"><div class="dl-main">${head}<div class="muted">${esc(m.t)}${m.lvl ? " · " + esc(m.lvl) : ""} · ${RND[m.r] || esc(m.r)} · ${fmtDay(m.d, m.ex)}</div></div>
    <div class="dl-side">${surfChip(m.s)}<div class="num">${esc(m.sc)}</div>${tail || ""}</div></div>`;
}
function playerBlock(p) {
  const facts = [p.country, p.hand, p.age ? `${String(p.age).replace(".", ",")} let` : "", p.height ? `${p.height} cm` : ""].filter(Boolean).join(" · ");
  const cz = (n, one, few, many) => `${n} ${n === 1 ? one : n >= 2 && n <= 4 ? few : many}`;
  const ld = (x) => `${cz(x.m, "zápas", "zápasy", "zápasů")}, ${cz(x.sets, "set", "sety", "setů")}`;
  return `<div class="pblock"><h4>${esc(p.name)}</h4><div class="muted">${esc(facts) || "údaje nejsou k dispozici"}</div>
    <div class="load"><span>3 dny: <b>${ld(p.load3)}</b></span><span>7 dní: <b>${ld(p.load7)}</b></span></div>
    ${p.exact ? "" : `<div class="muted">U starších zápasů známe jen začátek turnaje, zatížení je přibližné.</div>`}
    <div class="sub-h">Posledních ${p.last10.length} zápasů</div>
    ${p.last10.map((m) => matchLine(m, `<span class="wl ${m.won ? "w" : "l"}">${m.won ? "V" : "P"}</span> ${esc(m.opp)}`)).join("") || '<p class="muted">Žádné zápasy v datech.</p>'}
  </div>`;
}
async function toggleDetail(btn) {
  const card = btn.closest(".match");
  const open = card.querySelector(".detail");
  if (open) { open.remove(); btn.setAttribute("aria-expanded", "false"); btn.textContent = "Detail zápasu"; return; }
  btn.textContent = "Načítám…";
  const all = await loadDetails();
  const d = all && all[card.dataset.key];
  const box = document.createElement("div");
  box.className = "detail";
  if (!d) box.innerHTML = `<p class="muted">Detail pro tento zápas není k dispozici.</p>`;
  else {
    const m = S.today.matches.find((x) => `${x.tour}:${x.id}` === card.dataset.key);
    const names = [d.p1.name, d.p2.name];
    box.innerHTML = `<div class="sub-h">Vzájemné zápasy ${d.h2h.length ? `<b>${d.h2h_n[0]} : ${d.h2h_n[1]}</b>` : ""}</div>
      ${d.h2h.length ? d.h2h.map((h) => matchLine(h, `vyhrál(a) <b>${esc(names[h.win - 1])}</b>`)).join("") : '<p class="muted">Zatím spolu nehráli (v dostupných datech).</p>'}
      <div class="pgrid">${playerBlock(d.p1)}${playerBlock(d.p2)}</div>
      <p class="muted">Challengery a ITF jsou v datech jen do 5/2026. Model zatím používá jen Elo – tyto údaje jsou pro informaci.</p>`;
  }
  card.appendChild(box);
  btn.setAttribute("aria-expanded", "true"); btn.textContent = "Skrýt detail";
}

function openOddsForm(card) {
  if (card.querySelector(".odds-box")) { card.querySelector(".odds-box").remove(); return; }
  const key = card.dataset.key;
  const m = S.today.matches.find((x) => `${x.tour}:${x.id}` === key);
  const cur = oddsFor(m) || {};
  const box = document.createElement("div");
  box.className = "odds-box";
  box.innerHTML = `<div class="odds-row">
      <label class="field"><span>Kurz ${esc(m.p1.name)}</span><input class="o1" inputmode="decimal" value="${cur.o1 ?? ""}" placeholder="např. 1.85"></label>
      <label class="field"><span>Kurz ${esc(m.p2.name)}</span><input class="o2" inputmode="decimal" value="${cur.o2 ?? ""}" placeholder="např. 2.05"></label>
    </div>
    <label class="field"><span>Sázková kancelář (nepovinné)</span><input class="book" value="${esc(cur.book ?? store.get("book", ""))}" placeholder="např. Tipsport"></label>
    <div class="ev-live"></div>
    <div class="row-actions"><button class="btn save">Uložit</button>${cur.o1 ? '<button class="btn ghost del">Smazat</button>' : ""}
      <span class="muted status"></span></div>`;
  card.appendChild(box);
  const upd = () => {
    const o1 = parseOdd(box.querySelector(".o1").value), o2 = parseOdd(box.querySelector(".o2").value);
    box.querySelector(".ev-live").innerHTML = o1 && o2 ? evBlock(m.p1_prob, o1, o2, m.p1.name.split(" ").slice(-1)[0], m.p2.name.split(" ").slice(-1)[0], m.low_data) : "";
  };
  box.addEventListener("input", upd); upd();
  const status = box.querySelector(".status");
  const persist = async (entry) => {
    const lo = localOdds();
    if (entry) lo[key] = entry; else delete lo[key];
    status.textContent = "Ukládám do repozitáře…";
    box.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      await saveOddsRemote(key, entry);
      store.set("odds-local", lo);
      toast(entry ? "Kurzy uloženy. Vyhodnocení se přepočítá při příštím běhu." : "Kurzy smazány.");
      renderMatches();
    } catch (e) {
      status.textContent = e.message;
      box.querySelectorAll("button").forEach((b) => (b.disabled = false));
      if (/token/i.test(e.message)) $("#settings").showModal();
    }
  };
  box.querySelector(".save").onclick = () => {
    const o1 = parseOdd(box.querySelector(".o1").value), o2 = parseOdd(box.querySelector(".o2").value);
    if (!o1 || !o2) { status.textContent = "Zadej oba kurzy (větší než 1)."; return; }
    const book = box.querySelector(".book").value.trim();
    store.set("book", book);
    persist({ o1, o2, book, p1: m.p1.name, p2: m.p2.name, at: new Date().toISOString() });
  };
  const del = box.querySelector(".del");
  if (del) del.onclick = () => persist(null);
  box.querySelector(".o1").focus();
}

/* ---------- KALKULAČKA ---------- */
function calcPlayers() { return S.ratings[S.calcTour]; }
function findPlayer(name) {
  const n = name.trim().toLowerCase();
  if (!n) return null;
  const list = calcPlayers();
  return list.find((p) => p.name.toLowerCase() === n) || list.find((p) => p.name.toLowerCase().includes(n)) || null;
}
function fillDatalist() {
  $("#players").innerHTML = calcPlayers().map((p) => `<option value="${esc(p.name)}">`).join("");
}
/* pravděpodobnost na 2 vítězné sety -> na 3 vítězné (stejně jako elo.best_of_5) */
function bestOf5(p) {
  let lo = 0, hi = 1;
  for (let i = 0; i < 40; i++) { const s = (lo + hi) / 2; if (s * s * (3 - 2 * s) < p) lo = s; else hi = s; }
  const s = (lo + hi) / 2;
  return s ** 3 * (10 - 15 * s + 6 * s * s);
}
function daysSince(yyyymmdd) {
  const d = new Date(`${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6)}T12:00:00`);
  return Math.floor((Date.now() - d) / 864e5);
}
function renderCalc() {
  const out = $("#calc-out"), evOut = $("#calc-ev");
  const a = findPlayer($("#calc-a").value), b = findPlayer($("#calc-b").value);
  if (!a || !b) {
    out.innerHTML = `<p class="muted">Vyber dva hráče ze seznamu (${calcPlayers().length} aktivních hráčů ${S.calcTour.toUpperCase()}).</p>`;
    evOut.innerHTML = ""; return;
  }
  if (a.id === b.id) { out.innerHTML = `<p class="muted">Vyber dva různé hráče.</p>`; evOut.innerHTML = ""; return; }
  const k = S.calcSurface, scale = S.ratings.scale[S.calcTour];
  let p = 1 / (1 + Math.pow(10, -((a[k] - b[k]) * scale) / 400));
  const bo5 = S.calcTour === "atp" && $("#calc-bo5").checked;
  if (bo5) p = bestOf5(p);
  const side = (pl, pp, c) => {
    const off = daysSince(pl.last);
    return `<div class="side ${c}"><div class="who">${esc(pl.name)}</div><div class="pct num">${pct(pp, 1)}</div>
      <div class="sub num">férový kurz <b>${num(1 / pp)}</b></div>
      <div class="sub num">Elo ${Math.round(pl[k])} · ${pl.n} záp.</div>
      ${off >= S.ratings.long_break ? `<div class="flags"><span class="chip warn">⏸ ${off} dní bez zápasu</span></div>` : ""}
      ${pl.n < S.ratings.low_data ? `<div class="flags"><span class="chip info">málo dat</span></div>` : ""}</div>`;
  };
  out.innerHTML = `<div class="big">${side(a, p, "a")}${side(b, 1 - p, "b")}</div>
    <div class="bar" aria-hidden="true" style="margin-top:10px"><i style="width:${(p * 100).toFixed(1)}%"></i></div>
    <p class="muted">${k === "elo" ? "Celkové Elo bez ohledu na povrch." : "Mix celkového a povrchového Elo (" + SURF[k].toLowerCase() + ")."}${bo5 ? " Na 3 vítězné sety favorit vyhrává častěji." : ""}
      Počet zápasů zahrnuje i challengery a ITF.</p>`;
  const o1 = parseOdd($("#calc-o1").value), o2 = parseOdd($("#calc-o2").value);
  const unreliable = Math.min(a.n, b.n) < S.ratings.low_data;
  evOut.innerHTML = o1 && o2 ? evBlock(p, o1, o2, a.name.split(" ").slice(-1)[0], b.name.split(" ").slice(-1)[0], unreliable) : "";
}
function initCalc() {
  const bo5Wrap = () => { $("#calc-bo5-wrap").style.display = S.calcTour === "atp" ? "" : "none"; };
  seg($("#calc-tour"), S.calcTour, (v) => { S.calcTour = v; $("#calc-a").value = $("#calc-b").value = ""; fillDatalist(); bo5Wrap(); renderCalc(); });
  $("#calc-bo5").addEventListener("change", renderCalc); bo5Wrap();
  seg($("#calc-surface"), S.calcSurface, (v) => { S.calcSurface = v; renderCalc(); });
  ["#calc-a", "#calc-b", "#calc-o1", "#calc-o2"].forEach((s) => $(s).addEventListener("input", renderCalc));
  fillDatalist(); renderCalc();
}

/* ---------- ÚSPĚŠNOST ---------- */
function kpis(s, extra = "") {
  if (!s || !s.n) return `<p class="muted">Zatím žádné vyhodnocené zápasy.</p>`;
  return `<div class="kpis">
    <div class="kpi"><div class="v num">${pct(s.acc, 1)}</div><div class="l">trefa favorita</div></div>
    <div class="kpi"><div class="v num">${num(s.logloss, 3)}</div><div class="l">log-loss (méně = lépe)</div></div>
    <div class="kpi"><div class="v num">${num(s.brier, 3)}</div><div class="l">Brier (méně = lépe)</div></div>
    <div class="kpi"><div class="v num">${s.n.toLocaleString("cs-CZ")}</div><div class="l">zápasů${extra}</div></div></div>`;
}
function calibChart(series) {
  const W = 320, H = 220, L = 36, B = 28, T = 10, R = 10;
  const x = (v) => L + ((v - 0.5) / 0.5) * (W - L - R);
  const y = (v) => H - B - ((v - 0.4) / 0.6) * (H - B - T);
  let g = `<line class="diag" x1="${x(0.5)}" y1="${y(0.5)}" x2="${x(1)}" y2="${y(1)}"/>`;
  for (const t of [0.5, 0.6, 0.7, 0.8, 0.9, 1]) {
    g += `<line class="axis" x1="${x(t)}" y1="${H - B}" x2="${x(t)}" y2="${H - B + 4}"/><text class="lbl" x="${x(t)}" y="${H - 8}" text-anchor="middle">${t * 100}</text>`;
  }
  for (const t of [0.4, 0.6, 0.8, 1]) {
    g += `<line class="axis" x1="${L}" y1="${y(t)}" x2="${W - R}" y2="${y(t)}" stroke-opacity=".6"/><text class="lbl" x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${t * 100}</text>`;
  }
  for (const s of series) {
    const pts = s.data.filter((d) => d.n >= 5);
    if (!pts.length) continue;
    g += `<polyline fill="none" stroke="${s.color}" stroke-width="2" points="${pts.map((d) => `${x(d.pred)},${y(d.real)}`).join(" ")}"/>`;
    g += pts.map((d) => `<circle cx="${x(d.pred)}" cy="${y(d.real)}" r="4" fill="${s.color}"><title>${s.name}: předpověď ${pct(d.pred, 1)}, skutečnost ${pct(d.real, 1)} (${d.n} záp.)</title></circle>`).join("");
  }
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Kalibrace: předpovězená vs. skutečná úspěšnost favorita">${g}</svg>
    <div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join("")}<span>osa x: předpověď pro favorita (%), osa y: skutečnost (%)</span></div></div>`;
}
function renderTrack() {
  const t = S.track, css = getComputedStyle(document.documentElement);
  const c1 = css.getPropertyValue("--p1").trim(), c2 = css.getPropertyValue("--p2").trim(), c3 = css.getPropertyValue("--good").trim();
  const bt = t.backtest, live = t.live, od = t.odds;
  const rowS = (label, s) => s && s.n ? `<tr><td>${label}</td><td class="num">${s.n.toLocaleString("cs-CZ")}</td><td class="num">${pct(s.acc, 1)}</td><td class="num">${num(s.logloss, 3)}</td><td class="num">${num(s.brier, 3)}</td></tr>` : "";
  const thead = `<tr><th></th><th>zápasů</th><th>trefa</th><th>log-loss</th><th>Brier</th></tr>`;

  let oddsHtml = `<p class="explain">U každého zápasu můžeš zadat kurzy sázkovky. Model pak vsadí 1 jednotku na stranu s kladnou hodnotou
    (pravděpodobnost × kurz > 1). Skreč a kontumace se počítají jako storno, kurzy zadané až po začátku zápasu se nepočítají.
    Zápasy, kde má některý hráč méně než ${S.ratings.low_data} zápasů, jsou vyhodnocené zvlášť.</p>`;
  const oddsRow = (label, r) => `<tr><td>${label}</td><td class="num">${r.n}</td><td class="num">${r.wins}</td>
    <td class="num ${r.profit > 0 ? "pos" : r.profit < 0 ? "neg" : ""}">${num(r.profit)}</td><td class="num">${r.roi == null ? "–" : signPct(r.roi)}</td></tr>`;
  if (!od || !od.total) oddsHtml += `<p class="muted">Zatím nejsou zadané žádné kurzy.</p>`;
  else {
    oddsHtml += `<div class="scroll"><table class="t"><tr><th>hodnota nad</th><th>sázek</th><th>výher</th><th>zisk (j.)</th><th>ROI</th></tr>
      <tr><td colspan="5" class="muted" style="text-align:left">Spolehlivé zápasy</td></tr>
      ${od.by_threshold.map((r) => oddsRow(pct(r.min_ev), r)).join("")}
      ${od.low_data ? `<tr><td colspan="5" class="muted" style="text-align:left">Málo dat (jen pro informaci)</td></tr>${oddsRow(pct(0), od.low_data)}` : ""}
      </table></div><p class="muted">Zadaných zápasů: ${od.total}, tipů čeká na výsledek: ${od.tips_pending}.
      Počítají se jen tipy modelu ${esc(live.model)}${od.other_models ? ` (vynechané tipy starších modelů: ${od.other_models})` : ""}.</p>`;
    oddsHtml += od.entries.slice(0, 30).map((e) => {
      const tipName = e.tip ? (e.tip === 1 ? e.p1 : e.p2) : null;
      const res = e.profit == null ? (e.late ? "zadáno pozdě" : e.tip ? "čeká" : "bez sázky")
        : `<span class="${e.profit > 0 ? "pos" : "neg"}">${e.profit > 0 ? "+" : ""}${num(e.profit)} j.</span>`;
      return `<div class="list-item"><div class="l">${esc(e.p1)} – ${esc(e.p2)}${e.low_data ? ' <span class="chip info">málo dat</span>' : ""}<div class="muted">${num(e.o1)} / ${num(e.o2)}${e.book ? " · " + esc(e.book) : ""}${tipName ? " · tip " + esc(tipName) + " (" + signPct(e.ev) + ")" : ""}</div></div><div class="r">${res}</div></div>`;
    }).join("");
  }

  const recent = (live.all.recent || []).slice(0, 40).map((r) => {
    const fav = r.p1_prob >= 0.5 ? 1 : 2, hit = fav === r.winner;
    const fp = fav === 1 ? r.p1_prob : 1 - r.p1_prob;
    return `<div class="list-item"><div class="l"><b>${esc(fav === 1 ? r.p1 : r.p2)}</b> ${pct(fp)} vs ${esc(fav === 1 ? r.p2 : r.p1)}
      <div class="muted">${esc(r.tourney)} · ${ROUND[r.round] || esc(r.round)} · ${esc(r.score)}</div></div>
      <div class="r ${hit ? "pos" : "neg"}">${hit ? "✓" : "✗"}</div></div>`;
  }).join("");

  $("#track").innerHTML = `
    <h2 class="sec">Živá bilance</h2>
    <div class="model-note"><span class="chip info">model ${esc(live.model)}</span>
      <span>${esc(live.label)}. Tipy se počítají od <b>${fmtSince(live.since)}</b>.</span></div>
    <p class="explain">Jen tipy zveřejněné před začátkem zápasu (predikce se po začátku zápasu už nemění). Skreče a kontumace se nepočítají.
      Tipy starších verzí modelu se do bilance nemíchají.</p>
    ${kpis(live.all)}
    ${(live.previous || []).map((v) => `<p class="muted">Starší model ${esc(v.model)} (${esc(v.label)}, od ${fmtSince(v.since)}):
      ${v.n ? `vyhodnocené tipy: ${v.n}, trefa ${pct(v.acc, 1)}, log-loss ${num(v.logloss, 3)}` : "žádné vyhodnocené tipy"}.</p>`).join("")}
    ${live.all.n ? `<div class="scroll"><table class="t">${thead}${rowS("ATP", live.atp)}${rowS("WTA", live.wta)}</table></div>` : ""}
    ${live.all.n >= 30 ? calibChart([{ name: "živě", color: c3, data: live.all.calibration }]) : ""}

    <h2 class="sec">Tipy proti kurzům</h2>
    ${oddsHtml}

    <h2 class="sec">Zpětný test 2023 – dnes</h2>
    <p class="explain">Model před každým zápasem spočítá predikci a teprve pak se učí z výsledku, takže nevidí do budoucnosti. Hodnoceny jsou zápasy, kde oba hráči mají aspoň 10 zápasů.
      Pro srovnání: favorit podle žebříčku ATP vyhrál v ${pct(bt.atp.rank_acc, 1)}, WTA v ${pct(bt.wta.rank_acc, 1)} případů.</p>
    <div class="scroll"><table class="t">${thead}
      ${rowS("<b>ATP</b>", bt.atp)}${Object.entries(bt.atp.by_surface).filter(([k]) => SURF[k]).map(([k, v]) => rowS("&nbsp;&nbsp;" + (SURF[k] || k), v)).join("")}
      ${rowS("<b>WTA</b>", bt.wta)}${Object.entries(bt.wta.by_surface).filter(([k]) => SURF[k]).map(([k, v]) => rowS("&nbsp;&nbsp;" + (SURF[k] || k), v)).join("")}
    </table></div>
    <h2 class="sec">Kalibrace</h2>
    <p class="explain">Když model dává favoritovi 70 %, měl by favorit vyhrát zhruba v 70 % případů. Body na přerušované čáře = férové pravděpodobnosti.</p>
    ${calibChart([{ name: "ATP", color: c1, data: bt.atp.calibration }, { name: "WTA", color: c2, data: bt.wta.calibration }])}

    ${recent ? `<h2 class="sec">Poslední vyhodnocené tipy</h2>${recent}` : ""}`;
}

function fmtSince(iso) {
  return new Date(iso).toLocaleString("cs-CZ", { timeZone: TZ, day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/* ---------- nastavení ---------- */
function initSettings() {
  const dlg = $("#settings");
  $("#open-settings").onclick = () => {
    $("#set-repo").value = repoName(); $("#set-token").value = store.get("token", "");
    $("#set-status").textContent = store.get("token", "") ? "Token je uložený." : "Token zatím není uložený.";
    dlg.showModal();
  };
  const save = () => { store.set("repo", $("#set-repo").value.trim()); store.set("token", $("#set-token").value.trim()); };
  $("#set-repo").onchange = save; $("#set-token").onchange = save;
  $("#set-test").onclick = async () => {
    save(); $("#set-status").textContent = "Ověřuji…";
    try {
      const r = await gh("");
      $("#set-status").textContent = r.permissions && r.permissions.push ? `OK: zápis do ${r.full_name} povolen.` : `Repozitář vidím, ale token nemá právo zápisu.`;
    } catch (e) { $("#set-status").textContent = e.message; }
  };
  $("#set-clear").onclick = () => { store.del("token"); $("#set-token").value = ""; $("#set-status").textContent = "Token smazán."; };
  dlg.addEventListener("close", save);
}

/* ---------- start ---------- */
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  document.querySelectorAll(".bottom button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  window.scrollTo(0, 0);
}
async function load(name) {
  const r = await fetch(`data/${name}.json`, { cache: "no-cache" });
  if (!r.ok) throw new Error(name);
  return r.json();
}
async function main() {
  document.querySelectorAll(".bottom button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  const tab = location.hash.slice(1);
  showTab(["today", "calc", "track"].includes(tab) ? tab : "today");
  S.tour = store.get("tour", "all");
  initSettings();
  try {
    [S.today, S.ratings, S.track] = await Promise.all([load("today"), load("ratings"), load("track")]);
  } catch (e) {
    $("#matches").innerHTML = `<div class="empty">Data se nepodařilo načíst. Zkus obnovit stránku.</div>`;
    return;
  }
  const upd = new Date(S.today.data_fetched);
  $("#updated").textContent = "data " + upd.toLocaleString("cs-CZ", { timeZone: TZ, day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" });
  // lokální kurzy, které už jsou na serveru, můžeme zapomenout
  const lo = localOdds();
  for (const m of S.today.matches) { const k = `${m.tour}:${m.id}`; if (lo[k] && m.odds && m.odds.at === lo[k].at) delete lo[k]; }
  store.set("odds-local", lo);
  renderMatches(); initCalc(); renderTrack();
}
main();
