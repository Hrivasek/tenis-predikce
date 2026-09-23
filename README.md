# Tenis Elo – predikce zápasů ATP/WTA

Web: **https://hrivasek.github.io/tenis-predikce/** (mobilní, aktualizuje se sám každou hodinu 6:00–1:00).

Elo model pro dvouhru ATP a WTA s kalibrací pravděpodobností. Web ukazuje dnešní a zítřejší zápasy
s pravděpodobnostmi a férovými kurzy, kalkulačku hráč proti hráči podle povrchu a úspěšnost modelu
(zpětný test + živá bilance tipů, volitelně proti zadaným kurzům sázkovky).

## Jak to běží

| Soubor | Co dělá |
|---|---|
| `update.py` | stáhne z ESPN výsledky (posledních ~10 dní) a rozpis na 3 dny → `data/espn_*.csv`, `data/upcoming.json` |
| `build.py` | spočítá Elo, predikce, statistiky → `site/data/*.json`; vede log předzápasových tipů `data/predictions.csv` |
| `elo.py` | model (`python3 elo.py test` = zpětný test 2023+) |
| `mapping.py` | párování ESPN jmen na ID hráčů v historii (`data/aliases.json`) a povrch turnaje (`data/surfaces.json`) |
| `import_lower.py` | jednorázový import challengerů/ITF/kvalifikací a údajů o hráčích z archivu → `data/lower_*.csv.gz`, `data/players_*.csv` |
| `site/` | statický web (HTML + JS, bez závislostí) |
| `.github/workflows/update.yml` | každou hodinu 6:00–1:00 pražského času (úplné stažení v 6, 12, 18 h) + při změně kódu nebo `data/odds.json`: update → build → commit dat → GitHub Pages; běhy se nepřekrývají |

Kurzy sázkovky zadané na webu se ukládají přes GitHub API do `data/odds.json`
(fine-grained token jen pro tento repozitář, oprávnění *Contents: Read and write*, uložený v prohlížeči).

## Model (verze v2, od 23. 9. 2026)

- Elo celkové + povrchové (mix 50 : 50), K = 300 / (zápasy + 5)^0,4, grandslamy ×1,1.
- Historie zahrnuje i challengery, kvalifikace, ITF/Futures a WTA 125 (plná váha – nižší váhy nepomohly).
- Elo podle skóre: K se násobí podle podílu vyhraných gemů (síla 0,45 ATP / 0,6 WTA).
- Zápasy na 3 vítězné sety: pravděpodobnost se přepočítá přes pravděpodobnost výhry setu.
- Kalibrace: rozdíl ratingů × 0,85 (ATP) / 0,776 (WTA).
- „Málo dat“ = hráč má méně než 10 zápasů (všech úrovní).
- Vše naladěné na zápasech 2010–2022, testované na 2023+.

Zpětný test 2023+ (zápasy hlavní soutěže, oba hráči ≥ 10 zápasů na okruhu):

| | ATP trefa | ATP log-loss | WTA trefa | WTA log-loss |
|---|---|---|---|---|
| v1 (jen ATP/WTA, kalibrace) | 63,8 % | 0,627 | 65,1 % | 0,619 |
| **v2** | **65,4 %** | **0,615** | **66,6 %** | **0,603** |

Vyzkoušeno a **nepoužito** (žádný přínos na 2010–2022): penalizace / větší nejistota po dlouhé pauze,
nižší startovní rating nováčků (s challengery v historii nemá efekt).

Další faktory (23. 9. 2026) – logistická regrese nad Elo v2, výběr na 2019–2022 (fit 2010–2018),
finální fit 2010–2022, test 2023+ párově na stejných zápasech (rozdíl log-loss, záporné = lepší):

| Faktor | ATP | WTA | Verdikt |
|---|---|---|---|
| vzájemné zápasy (celkově + povrch) | −0,0002 ± 0,0002 | −0,0001 ± 0,0002 | šum |
| únava (zápasy/sety/gemy za 3 a 7 dní) | −0,0004 ± 0,0003 | −0,0008 ± 0,0004 | hranice šumu, v provozu zkreslené (chybí challengery po 5/2026) |
| levák, výška (i podle povrchu) | +0,0001 ± 0,0003 | +0,0000 ± 0,0001 | nic |
| domácí prostředí | +0,0001 ± 0,0003 | −0,0002 ± 0,0003 | nic |
| krátkodobá forma nad Elo (10 zápasů) | −0,0000 ± 0,0002 | −0,0001 ± 0,0003 | nic |
| **statistiky podání a returnu** (EW body vyhrané na podání + na returnu) | **−0,0028 ± 0,0006** | **−0,0014 ± 0,0006** | pomáhá, ale **nenasaditelné** – pro nové zápasy nemáme zdroj |

→ Verze v3 nevznikla. Pokud se objeví zdroj statistik podání/returnu pro nové zápasy (včetně challengerů),
je to nejslibnější další krok (cca čtvrtina zisku v2 u ATP).

**Při změně modelu** přidej novou verzi do `MODELS` v `elo.py` a nastav `MODEL_VERSION` – živá bilance
se pak počítá od začátku nové verze a tipy starších verzí se nemíchají.

## Zdroje dat (zjištěno 23. 9. 2026)

### Použité

| Zdroj | Co | Poznámka |
|---|---|---|
| [Aneeshers/tennis-sackmann-archive](https://github.com/Aneeshers/tennis-sackmann-archive) | historie ATP/WTA, challengery, kvalifikace, ITF/Futures do 25. 5. 2026 | zrcadlo Sackmannových dat (CC BY-NC-SA 4.0). Uloženo v repozitáři (`data/*_all.csv`, `data/lower_*.csv.gz`), kdyby archiv zmizel. |
| ESPN `site.api.espn.com/apis/site/v2/sports/tennis/{atp,wta}/scoreboard?dates=YYYYMMDD` | výsledky a rozpis ATP a WTA (včetně kvalifikací a WTA 125) | zdarma, bez klíče, funguje z GitHub Actions. **Vrací 403 pro některé User-Agenty včetně prohlížečových** – výchozí urllib/curl projde. Rozsahy `dates=A-B` občas vrátí prázdno → stahujeme po dnech. Neuvádí povrch (odvozujeme z historie, `data/surfaces.json`). **Nemá challengery ani ITF.** |

### Nepoužité / nefunkční

| Zdroj | Stav | Proč |
|---|---|---|
| [JeffSackmann/tennis_atp, tennis_wta](https://github.com/JeffSackmann) | ❌ 404 | původní repozitáře zmizely |
| Sofascore API | ❌ 403 | blokuje roboty (lokálně i z GitHub Actions) |
| atptour.com | ❌ 403 | ochrana proti robotům |
| itftennis.com – výsledky | ❌ | Incapsula ochrana; **kalendář** (`/tennis/api/TournamentApi/GetCalendar`) z Actions funguje |
| Tennis Abstract | ⚠️ jen lokálně | viz níže |
| tennis-data.co.uk | ⚠️ | soubory 2026 nenalezeny; nemá challengery (jen kurzy hlavního okruhu) |
| **Tennis Explorer** | ⚠️ funguje jen z Actions | výsledky challengerů i ITF (`/results/?type=atp-single|itf-men-single|itf-women-single&year=&month=&day=`) + kurzy sázkovek. **Z českých IP přesměruje na blokovací stránku** (nejde ladit lokálně). Jde o scraping HTML (podmínky webu), jména zkrácená („Mmoh M.“ → nutné párování přes profily hráčů), chybí kolo a povrch. **Zatím nestavěno** (rozhodnutí 23. 9. 2026). |

### Tennis Abstract (ověřeno 23. 9. 2026 odpoledne)
- **Aktuální výsledky jsou:** stránky turnajů `/current/<rok><turnaj>.html` („Results and Forecasts“) pro ATP, WTA,
  challengery i WTA 125 – kolo, plná jména, země, nasazení/WC/Q, skóre, nadcházející zápasy, forecast. Chybí datum zápasu,
  povrch a **statistiky podání** (ty jsou jen u stránek hráčů, které je načítají z `/jsfrags/`).
- **robots.txt:** zakázané jen `/jsfrags/`, `/jsmatches/`, `/jsplayers/` → stránky turnajů a Elo žebříčky povolené,
  statistiky hráčů (jsfrags) ne. Dopolední „zastaralá data“ byla ze zakázaného `/jsmatches/`.
- **Podmínky webu:** samostatnou stránku nemá; datasety autora jsou pod CC BY-NC-SA 4.0 (uvést zdroj, nekomerčně).
- **Elo žebříčky** (`/reports/atp_elo_ratings.html`, `wta_elo_ratings.html`) – celkové + povrchové Elo, aktualizace týdně;
  použitelné jako kontrola našeho modelu.
- **Z GitHub Actions nefunguje:** všechny stránky 403 s Cloudflare výzvou („Just a moment“) – obcházet ji nebudeme.
  Z domácí sítě stránky normálně jdou → jediná cesta je stahovat z vlastního počítače (plánovaná úloha 1–2× denně) a výsledek pushnout.

### Proč na challengerech záleží
Bez průběžných výsledků challengerů/ITF přínos v2 postupně mizí: v testu na 2025–26 s historií
nižších úrovní useknutou na konci 2024 měl ATP log-loss 0,628 (≈ jako bez challengerů, 0,628)
místo 0,620; WTA 0,612 místo 0,606 (bez challengerů 0,618). **Pro ATP přínos zmizí zhruba do roka.**

### Placená API s challengery a ITF (orientační ceny, září 2026)

| Služba | Cena | Limit | Challenger / ITF | Poznámka |
|---|---|---|---|---|
| [Live Tennis API](https://livetennisapi.com/) | free / **$9,99** / $29,99 / $99,99 měsíčně | 100 / 1 000 / 10 000 / 500 000 požadavků denně | ✅ výsledky až od placeného Basic | free plán nemá dohrané zápasy; archiv výsledků 1968–2022 |
| [Tennis API (tennis-api.com)](https://tennis-api.com/api-pricing/) | **$10** / $39 měsíčně | 10 000 / 75 000 požadavků měsíčně | ✅ | menší plány i přes RapidAPI; nad limit $0,002/požadavek |
| [API-Tennis (api-tennis.com)](https://api-tennis.com/) | $40 / $60 / $80 / $120 měsíčně | 8 000–2 000 000 požadavků denně | ✅ (typy „Challenger Men Singles“, „Itf Men/Women Singles“) | 14denní zkušební verze, obsahuje i kurzy |
| [Goalserve](https://www.goalserve.com/en/sport-data-feeds/tennis-api/prices) | $150 měsíčně, $1 200 ročně | – | ✅ | 30denní zkušební verze |
| [Apify – Tennis Explorer scraper](https://apify.com/oswaldocarabano/tennisexplorer-scraper/api) | $0,0015 za výsledek (první 10 000), pak $0,0003 | platba za použití | ✅ | cca $4 za týden výsledků; stejná data jako Tennis Explorer, scraping za nás |
| Sportradar | stovky až tisíce $ měsíčně | smlouva | ✅ | bez veřejného ceníku, 30denní trial |

Pro náš objem (pár desítek požadavků denně) by stačil nejlevnější placený plán kolem **$10 měsíčně**
(Live Tennis API Basic nebo tennis-api.com). Před nákupem ověřit na zkušební verzi: pokrytí ITF,
stabilní ID hráčů a jak se dají spárovat se Sackmannovými ID (jména, země, datum narození).

Napojení nového zdroje: stáhnout výsledky do formátu `data/lower_*.csv.gz` (sloupce viz `import_lower.py`)
nebo do vlastního CSV a přidat ho v `elo.load_matches`; hráče párovat přes `mapping.PlayerMap`.

## Licence dat
Historická data © Jeff Sackmann / Tennis Abstract, [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) –
nekomerční použití s uvedením zdroje. Web je jen pro zábavu, nejde o investiční radu.
