# StatsHunters Route Planner

Nástroj pro plánování běžeckých tras podle statistik ze [StatsHunters](https://www.statshunters.com) (explorer tiles). Cíl: doporučovat trasy, které

1. vylepší statistiky (max square, max cluster, nové tiles),
2. vedou místy, kde jsem dlouho neběhal.

## Spuštění

```bash
# webový server (FastAPI + Leaflet mapa)
python src/main.py                # http://127.0.0.1:8000
python src/main.py --port 8001    # jiný port
python src/main.py --host 0.0.0.0 --port 8000

# jen výpis statistik do konzole (bez serveru)
python src/main.py --stats

# stažení čerstvých dat ze StatsHunters a konec (vyžaduje share link, viz Konfigurace)
python src/main.py --sync

# pro vývoj: server se sám restartuje při změně kódu
python src/main.py --reload
```

Pozor: bez `--reload` platí, že statické soubory (`src/web/`) se čtou z disku vždy čerstvé,
ale python kód drží server z doby svého startu — po změně backendu je potřeba restart.

Spouštět z kořene repozitáře. Frontend běží přímo na kořenové URL (`/`), API pod `/api/...`.

### Prostředí

- Python 3.13+ (vyvíjeno na 3.14), závislosti: `pip install -r requirements.txt`
  (fastapi, uvicorn, pyyaml, shapely, osmnx — s ním geopandas/numpy/pandas/networkx)
- V repu není venv — balíčky jsou nainstalované v globálním Pythonu.

## Data

- `data/activities*.json` — export aktivit ze StatsHunters API (osobní data, jsou v `.gitignore`).
  Formát: `{"activities": [{id, date, type, distance, tiles: [{x, y}], ...}], "meta": {...}}`.
- Tiles odpovídají dlaždicím OSM na **zoom 14** (standard StatsHunters).
- Do tile statistik se počítají **jen aktivity typu `Run`** (natvrdo v `src/tiles.py`).
- Aktuální stav dat (07/2026): 2 261 aktivit, 896 run tiles celkem.

Data se dají stáhnout automaticky ze StatsHunters share API (stránkovaně po 500 aktivitách):
`python src/main.py --sync` z příkazové řádky, nebo tlačítkem **Aktualizovat data ze StatsHunters**
přímo v mapě (`POST /api/sync` — po stažení sám vyčistí cache a UI se znovu načte).
Stažení přepíše soubory `data/activities{N}.json`; přebytečné vyšší stránky smaže.

## Konfigurace — `config.yaml`

```yaml
home:                  # výchozí bod tras
  name: Karlovo namesti
  lat: 50.0757
  lon: 14.4188
target_distance_km: 15       # cílová délka trasy
distance_tolerance_km: 3     # tolerance ±
run_pace_min_per_km: 6.0     # tempo běhu (pro časový rozpočet výprav)
expedition_budget_min: 120   # výchozí časový rozpočet celé výpravy
statshunters:
  share_link: ""             # https://www.statshunters.com/share/<kod> nebo jen <kod>
```

Share link se vytváří na statshunters.com → Settings → Share link. Místo configu jde zadat
i proměnnou prostředí `STATSHUNTERS_SHARE_LINK` (má přednost — hodí se, když kód nechcete
mít v commitovaném `config.yaml`). `STATSHUNTERS_BASE_URL` přepíše adresu API (pro testy).

Pozn.: hodnoty z configu jsou jen **výchozí** — start, délka, tolerance, tempo i časový
rozpočet se zadávají za běhu v panelu mapy (a jako parametry `POST /api/route`
resp. `/api/expedition`).

## Struktura projektu

| Soubor | Účel |
|---|---|
| `src/main.py` | vstupní bod — argparse + uvicorn |
| `src/api.py` | FastAPI endpointy, cache, definice období (all / year / recent = 3 měsíce) |
| `src/load.py`, `src/models.py` | načtení JSON exportů → `Activity`, `Tile` |
| `src/tiles.py` | tile databáze (visit_count, first/last_visit) s filtrem podle období |
| `src/frontier.py` | hraniční tiles (nenavštívení sousedé navštívených) |
| `src/cluster.py` | největší 4-souvislý cluster navštívených tiles |
| `src/square.py` | největší plně pokrytý čtverec (DP) |
| `src/scoring.py` | bodování kandidátních tiles: 9 priorit (square/cluster/nenavštívený × 3 období) + bonus za stáří poslední návštěvy |
| `src/statshunters.py` | klient StatsHunters share API — stránkované stahování aktivit do `data/` |
| `src/routing.py` | plánování běhů: pěší graf OSM (osmnx), výběr tiles, preference cest, penalizace opakování, GPX |
| `src/transit.py` | síť MHD z PID GTFS + router spojení (min. přestupů, priorita druhů) |
| `src/expedition.py` | výpravy: cílové oblasti, spojení tam/zpět, časový rozpočet |
| `src/landmarks.py` | vodní toky a železnice (osmnx features) + geometrické křížení pro itinerář |
| `src/geojson.py` | převod tiles na GeoJSON polygony |
| `src/web/` | Leaflet frontend (mapa, přepínání vrstev, statistiky, legenda, sync tlačítko) |

### Barvy na mapě

Každý navštívený tile se kreslí jednou, barvou podle **období poslední návštěvy** (studená → teplá):
modrá `#2a78d6` = naposledy před letoškem, žlutá `#eda100` = letos (před více než 3 měsíci),
červená `#e34948` = poslední 3 měsíce. Doporučené tiles: fialová výplň `#4a3aa7` u dosud
nenavštívených; doporučení na už navštíveném tile má jen tmavý obrys bez výplně, aby
nepřekrylo barvu jeho období (tu informaci nese sám tile). Paleta je ověřená validátorem
na rozlišitelnost včetně barvosleposti (nejhorší pár období ΔE 15,3, cíl ≥ 8). Obrysy max
clusteru (čárkovaně) a max square (plně) používají barvu svého období a jsou neklikatelné,
aby nepřekrývaly popupy tiles a doporučení.

## API

| Endpoint | Popis |
|---|---|
| `GET /api/health` | liveness check |
| `GET /api/summary` | počty aktivit, config, metriky pro všechna období |
| `GET /api/periods/{period}/tiles` | navštívené tiles (GeoJSON); `period` = `all` \| `year` \| `recent` |
| `GET /api/periods/{period}/frontier` | hraniční tiles |
| `GET /api/periods/{period}/cluster` | obrys největšího clusteru |
| `GET /api/periods/{period}/square` | obrys největšího čtverce |
| `GET /api/opportunities` | doporučené tiles seřazené podle skóre (rank, důvody, přínosy, stáří poslední návštěvy) |
| `POST /api/route` | naplánuje okruh; JSON body `{lat, lon, distance_km, tolerance_km}` (vše volitelné, výchozí z configu); vrací délku, waypointy, protnuté tiles, souřadnice i GPX |
| `POST /api/expedition` | naplánuje celou výpravu (běh + volitelně MHD); body navíc `{budget_min, pace_min_per_km}`; vrací segmenty (běh na zastávku / MHD / okruh / návrat), časy a alternativní směry |
| `POST /api/sync` | stáhne čerstvá data ze StatsHunters, přepíše `data/` a vyčistí cache |
| `GET /api/tiles`, `/api/frontier`, … | aliasy pro období `all` |

Všechny odpovědi se počítají při prvním dotazu a drží v `lru_cache` — po ruční změně dat je potřeba restart serveru; `POST /api/sync` si cache čistí sám.

## Jak se počítá skóre doporučených tiles

Kandidáti = hraniční tiles všech období + tiles nenavštívené letos / v posledních 3 měsících.
Skóre tile má dvě složky:

1. **Priority** — 9 pravidel (zvětšení square / clusteru / nenavštívenost × období celkem → letos → 3 měsíce) s explicitními váhami: poměr **4 : 2 : 1 uvnitř období** (square : cluster : nenavštívený) a **16× odstup mezi obdobími** (celkem 2048/1024/512, letos 128/64/32, 3 měsíce 8/4/2) — výrazná převaha delších období, např. růst ročního clusteru přebije růst 3měsíčního square.
2. **Stáří poslední návštěvy** — spojitý bonus 0–1: nikdy nenavštívený tile = 1,0; jinak `dny od poslední návštěvy / 1095` (strop 3 roky). Bonus je menší než minimální rozestup mezi kombinacemi priorit (2), takže jen doladí pořadí mezi tiles se stejnými prioritami — „kde jsem dlouho nebyl" vyhrává.

**Zásada (důležité pro budoucí plánování tras):** skóre tile je *čistý přínos* jeho návštěvy — nikdy nesmí obsahovat náklady na cestu (MHD, vzdálenost od domova). Optimalizace trasy bude maximalizovat součet přínosů tiles na trase; kdyby byla cena dopravy ve skóre, započítala by se tolikrát, kolika tiles trasa projde. Náklady na dopravu patří až na úroveň trasy, jednou za trasu.

## Plánování tras

Zvolený stack: **osmnx + networkx** (čistý Python, ověřeno na Pythonu 3.14). Externí routery
(BRouter, Valhalla, OSRM) nejsou potřeba — kombinatorika výběru tiles musí být v našem kódu
tak jako tak a graf v procesu dává plnou kontrolu nad cenovou funkcí.

**V mapě (hlavní cesta):** sekce „Planovani trasy" v panelu — start se volí přetažením
špendlíku nebo pravým kliknutím do mapy, délka a tolerance v polích (výchozí z configu),
tlačítko naplánuje trasu přes `POST /api/route`, vykreslí ji a nabídne stažení GPX.
První výpočet po startu serveru trvá ~40 s (načtení grafu + scoring do paměti), každé
další přeplánování je pod sekundu.

```bash
# totez z prikazove radky
python src/routing.py --gpx route.gpx
python src/routing.py --lat 50.1030 --lon 14.4500 --distance 10 --tolerance 2
```

Jak to funguje:

1. **Pěší graf OSM** se stáhne z Overpass API kolem startu (poprvé jednotky minut) a cachuje
   do `data/walk_<lat>_<lon>_<reach>km.graphml`. Cache je podle **pokrytí**: použije se
   jakýkoli uložený graf, jehož kruh pokrývá požadovaný start + dosah (s tolerancí 1,5 km —
   dosah je horní odhad a chybějící vnější lem trasu nerozbije); stahuje se velkoryse
   (min. 10 km), takže změna délky ani startu v okolí nevyvolá nové stahování. Načtený graf
   zůstává v paměti serveru.
2. **Výběr trasy porovnáním variant**: staví se portfolio okruhů — rank-greedy seed
   (cheapest insertion na odhadech vzdušná čára × 1,35), seedy kolem **skupin
   sousedících kandidátů** (4-okolí) a **seedy na dokompletování square** (okna
   (side+1)² s ≤ 4 chybějícími tiles v dosahu — jednotlivé chybějící tiles mají
   samy o sobě nulový square přínos, proto je obecné hledání nemá důvod kombinovat).
   Každá varianta se exaktně přepočítá (`bidirectional_dijkstra` vážená
   **preferencemi typů cest**: cyklostezka 0,60 > pěšina/turistická cesta 0,70 >
   pěší zóna 0,80 > chodník 0,85 > klidná ulice 1,0; rušné silnice ×1,35–3,0 a
   schody ×1,4 penalizované — délková tolerance se ale vždy kontroluje proti
   skutečným metrům) a ohodnotí **společným přínosem všech protnutých tiles**
   (`scoring.evaluate_tile_set`): Δsquare a Δcluster se počítají s celou množinou
   najednou (zisky nejsou aditivní), plus počty nových tiles podle období a
   staleness bonusy. Váhy: priorita (viz tabulka výše) × velikost zisku, přičemž **square se váží
   plochou** (side² − baseline²) — bez toho by snadný růst clusteru o pár tiles
   vždy přebil vzácný růst square a obrátil pořadí priorit. Vítěz se ještě zkouší
   vylepšit přidáváním nevyužitých kandidátů (2 kola). Při přetečení tolerance
   odpadá nejslabší waypoint; naopak pokud trasa nedosáhne spodní hranice, dotáhne
   se přes další tiles v dosahu (`_extend_to_window`).
3. **Kudy tile protnout**: waypoint není střed dlaždice, ale uzel uvnitř ní, který
   nejméně zajíždí z předchozího bodu k dalšímu cíli — trasa se tak dlaždice dotkne
   tam, kudy stejně vede. Kvůli chybě GPS/navigace se drží **rezerva 75 m od hranice**
   (`TILE_MARGIN_M`), takže trasa vede prokazatelně dovnitř; naměřená hloubka průniku
   na reálných trasách je 220–540 m. Odhady délky s tím počítají (waypoint má
   efektivní poloměr ~700 m) — bez té korekce nadhodnocovaly délku 1,5–4× a trasy
   vycházely zbytečně krátké.
4. **Ořez ocásků**: slepé úseky tam-a-zpět se zkracují na nejmenší délku, která
   zachová množinu protnutých tiles — přínos se nemění, hluchá vzdálenost mizí.
   Navíc se **penalizuje průchod stejnou ulicí**: hrana už použitá na trase se při
   plánování dalšího úseku zdraží, takže se okruh vrací jinudy. Nízkoopakovací
   varianty se počítají rovnou v portfoliu (top 3 seedy, `AVOID_VARIANTS`), ne až
   jako oprava vítěze. Opakování je měkká složka cílové funkce: skóre = přínos ×
   (1 − 0,5 × **podíl** opakovaných metrů). Podíl, ne absolutní kilometry — s
   absolutní penalizací vycházel vždy nejlevněji nejkratší přípustný okruh.
   Míra vyhýbání je laditelná konstantou `REPEAT_PENALTY_FRACTION`.
5. **Itinerář běhu** (`route_directions` + `src/landmarks.py`) — tahák na trasu: úseky
   se stejným popisem sloučené, s **kumulativní vzdáleností od startu** (akční bod:
   „v 0,5 km vpravo Ke Karlovu"), směrem zatočení, „po schodech", označením mostů,
   u prvního kroku světovou stranou a **orientačními body s km** (co a kde trasa kříží).
   - **Názvy ulic**: pěší graf z osmnx vede chodníky jako **nepojmenované** cesty a osy
     ulic z velké části vynechává (Ke Karlovu 0 hran, Ječná 2). Pojmenované ulice se
     proto stahují **zvlášť** (osmnx features, `data/streets_*.json`) a při přípravě
     grafu se každému chodníku přiřadí ulice, podél níž vede (atribut `along_street`,
     KD-strom, ~0,7 s). Pokrytí názvy stouplo z ~16 % na **~92 %** délky, generický
     „chodník" zbyl minimálně. (Ověřeno proti ručnímu popisu z mapy: začátek trasy
     „Ječná → vpravo Ke Karlovu → Wenzigova → Lublaňská → Bělehradská" sedí.)
   - **Orientační body**: křížené významné ulice (tertiary+, kolmé ke směru) z ulic
     nad rámec grafu; a **vodní toky + železnice** (v pěším grafu nejsou) — vodní toky
     si nesou název (Botič, Vltava), tratě genericky „žel. trať". Křížení se hledá
     geometricky (shapely) a řadí podle vzdálenosti; ulice, po níž právě běžíme nebo
     hned poběžíme, se jako křížení neuvádí.
   - Zdroje (ulice, bariéry) se cachují coverage cache jako graf; první stažení pro
     Prahu je v řádu minut (geometrie Vltavy, 25 tis. ulic), pak 0,1 s. Krátké úseky
     se slučují (bezejmenné pod 250 m, pojmenované pod 100 m). V panelu pod
     rozbalovacím „Itinerar behu".
   - **Odečet vzdálenosti v mapě**: najetím na trasu se ukáže, kolik km je daný bod
     od startu běhu a kolik zbývá (jako v mapy.cz), s kroužkem na nejbližším bodě
     trasy. Slouží k přesnému popisu míst („nepřesnost v 3,2 km"). Počítá se v
     prohlížeči z bodů trasy — souhlasí s délkou i itinerářem na jednotky metrů.
6. **Výstup**: délka, waypoint tiles, všechny protnuté tiles (počítané z plné
   geometrie hran — trasy v mapě i GPX kopírují skutečné tvary ulic), rozpad
   přínosu, itinerář, počet porovnaných variant, GPX.

Naměřeno (Praha, okolí Karlova náměstí, graf 141 810 uzlů): plánování okruhu
**0,1–10 s** podle délky a počtu variant (15±3 km ≈ 10 s, kratší okruhy pod 1 s);
jednorázově načtení grafu z cache ~24 s a scoring ~9 s (obojí si server podrží
v paměti).


## Výpravy s MHD

Výprava = [běh na zastávku] → MHD → **běh** → MHD → [běh domů], s rozpočtem na celkový
čas (`expedition_budget_min`, výchozí 120 min). Pěší přesuny se plánují po stejném pěším
grafu jako běhy a počítají se do kilometrů běhu; čas běhu se odhaduje tempem
`run_pace_min_per_km`. Návrat může vést **z jiné zastávky** než výstup — běh pak cílovou
oblast přejde z bodu do bodu, místo aby se vracel. Tlačítko **Naplanovat vypravu (s MHD)**
v panelu; čistý okruh bez MHD je vždy jednou z porovnávaných variant.

1. **Síť MHD** z veřejného GTFS feedu PID (`data/pid_gtfs.zip`, ~44 MB, stáhne se
   automaticky; kompaktní graf ~2 MB se cachuje v `data/transit_graph.json`). Technické
   kolejové body feedu (prefix `T…` — kilometrovníky, „Pha hl.n. Lc…" se souřadnicemi
   mimo nádraží) se kontrahují a **noční linky** (příznak `is_night`) se vynechávají
   úplně — běhy se plánují přes den. Časy jízdy
   z jízdních řádů (reprezentativní spoj každé linky a směru); čekání = **polovina
   intervalu linky** pro daný typ dne (všední den / víkend, medián rozestupů odjezdů
   z GTFS calendar, ořez 1–20 min). Kromě intervalu se ukládá i **počet spojů**:
   linka, která v daný typ dne nejede, se přeskočí úplně; linka s jediným spojem
   denně dostane strop čekání (dřív spadla na paušál podle druhu dopravy a router
   ji nabízel, jako by jezdila každých 12 minut).
   Typ dne se v UI přepíná („Vikendove intervaly MHD", výchozí podle dnešního data).
2. **Router spojení** minimalizuje primárně počet přestupů (penalizace 30 min), sekundárně
   čas vážený prioritou druhů: metro ×1,0 > tram ×1,15 > vlak ×1,25 > ostatní ×1,5.
   Do ceny se započítá i **doběh z domova na nástupní a z výstupní zastávky domů**
   (v minutách běhu, ne do času jízdy), takže vyhraje zastávka výhodná pro celou
   výpravu — bližší zastávka porazí vzdálenější se stejně rychlým spojením.
   Vystupuje se vždy tam, kam skutečně dojela linka (dřív mohl router na konci
   „přestoupit" pěšky jinam a běh pak začínal na jiné zastávce, než kde jízda končila).
   Přestupy mezi stejnojmennými zastávkami platí jen do 600 m — PID má stejná jména
   obcí/zastávek i desítky kilometrů od sebe (rekord: 4× „Osek", 116 km).
3. **Cílové oblasti**: lokální skupiny sousedících kandidátů (velké souvislé fronty se
   dělí mřížkou) **plus okna na dokompletování max square** (globální scan přes
   integrální obraz — chybějící tiles okna bývají rozptýlené a skupinové cíle by je
   nezachytily). Oblasti se řadí podle společného přínosu; předfiltr dosažitelnosti
   vyřadí ty, ke kterým se v rozpočtu nedá dojet. Pro top oblasti se najde spojení
   na zastávku v doběhu oblasti a spočte časové okno pro okruh (zpáteční spojení se
   uvažuje symetrické).
4. **Exaktní plán** se počítá pro čistý okruh + nejlepší až 3 MHD kandidáty (přednost mají
   zastávky s už staženým pěším grafem). Zpáteční zastávka se volí mezi zastávkami u cíle:
   dobré spojení domů (cena ≤ nejlepší + 15 ekviv. min) a co nejdál od výstupu, aby běh
   oblast přešel; pěší přesuny domov ↔ zastávka se počítají exaktně po grafu (kreslí se
   tečkovaně). Vítěz podle skutečného přínosu běhu. Odpověď obsahuje segmenty s časy,
   spojení (linky, přestupy) a alternativní směry. V mapě: MHD čárkovaně po
   jednotlivých zastávkách (s markery a tooltipy), start běhu zeleně / konec červeně,
   mezery mezi segmenty spojené tečkovaně; v panelu podrobný itinerář (každá jízda
   zvlášť: linka, odkud → kam, počet zastávek, čas, čekání, přestupní zastávky).

## Stav vývoje (2026-07-18)

**Hotové (commitnuté):** mapa s tiles pro 3 časová období, obrysy max clusteru a max square, panel se statistikami, scoring doporučených tiles (`/api/opportunities`).

**Nové (zatím necommitnuté):**
- plánování okruhů (`src/routing.py` + `POST /api/route` + UI v mapě) — špendlík startu, délka/tolerance v panelu, vykreslení trasy, GPX ke stažení; po warm-upu přeplánování 0,3–1,7 s,
- **optimalizace společného přínosu**: trasa se vybírá porovnáním variant podle skutečného zisku statistik celé množiny protnutých tiles; square vážený plochou + seedy na dokompletování square (ověřený postup zlepšení z Karlova nám.: greedy 18,6 → portfolio 36,9 → square-aware 96,6, trasa 4×4 → 5×5 přes Košíře); rozpad přínosu se zobrazuje v panelu,
- **kvalita tras**: preference typů cest pro běh (cyklostezka > turistická cesta > park/pěší zóna > chodník > klidná ulice; rušné silnice penalizované) — ověřeno: trasa Karlovo nám. → Podolí přešla z 77 % preferovaných / 21 % silnic na **98 % preferovaných / 1 % silnic** při stejné délce; trasy a GPX nově kopírují skutečné geometrie ulic (896 bodů místo ~370 uzlů),
- **kvalita okruhů (07/2026)**: waypointy míří dovnitř dlaždice místo do jejího středu (rezerva 75 m proti chybě GPS), penalizace opakování je podílová a nízkoopakovací varianty soutěží rovnou v portfoliu. Na výchozím okruhu z Karlova nám.: opakování **24 % → 3 %**, zamotaná Trója zmizela (**40 % → 0 %** bodů trasy v oblasti) a přínos vzrostl **90 → 209**. Kalibrace odhadů délky (waypoint má efektivní poloměr) navíc odstranila systematicky krátké trasy — všech 7 testovaných scénářů je nyní v toleranci (dřív 2 mimo),
- **výpravy s MHD** (`src/transit.py` + `src/expedition.py` + `POST /api/expedition` + tlačítko v UI) — ověřeno E2E: z Karlova nám. při 15±3 km / 120 min vyhrála výprava metro A + bus 350 do Roztok (benefit 1371 vs. 96,6 čistého okruhu, 3 nové tiles, 117,7 min); při 12±3 km / 150 min vlak T7 do Dobřichovic (0 přestupů) s dokompletováním **celkového square 15×15 → 16×16** (benefit 17 138, 148,4 min) — shoduje se s intuicí uživatele (Černošice/Solopisky), která na 120 min opravdu nevychází.

**Empirické zjištění (07/2026):** v doběhovém dosahu z Karlova náměstí (~8 km) je už všechno
navštívené i letos — lokálně jde zlepšovat jen 3měsíční metriky. Velké zisky (nové tiles,
celkový square/cluster) leží na okrajích navštíveného území, tj. vyžadují jiný start nebo
dopravu — to dává prioritu bodu „Dosažitelnost MHD" níže.

## Další kroky (návrh)

1. **Asynchronní příprava nové oblasti** — `POST /api/route` v úplně nové oblasti blokuje na minuty (Overpass download) a hrozí timeout prohlížeče; převést na úlohu na pozadí s hlášením průběhu do UI.
2. **Kvalita okruhu** — společný přínos množiny, ořez ocásků, geometrie hran, preference typů cest i penalizace opakování ulic jsou hotové; zbývá případně: konfigurovatelné faktory preferencí a penalizace opakování v `config.yaml`, jemnější rozlišení chodník vs. cesta v parku (vyžaduje tagy `footway`/`surface` navíc v osmnx `useful_tags_way` + nové stažení grafů). Náklady na dopravu odečítat **jednou za trasu**, nikdy per tile.
3. **Výpravy s MHD — další iterace** — v1 hotová (viz výše); zbývá: nesymetrický návrat (jiná zastávka / jiné spojení zpět), běh z bodu do bodu (MHD tam, doběh domů nebo na jinou zastávku), reálné intervaly linek místo paušálního čekání (GTFS frequencies), přesnost pěších přesunů (teď vzdušná čára × 1,3). Náklady na dopravu zůstávají route-level, nikdy ve skóre tile.
4. **Výkon `/api/opportunities`** — `_measure_gain` přepočítává celý cluster/square pro každého kandidáta; s růstem dat zvážit inkrementální výpočet a persistentní cache (teď jen `lru_cache` do restartu).
5. **Testy** — v repu zatím žádné; pytest pro `cluster`, `square`, `scoring`, `routing` (čisté funkce), `statshunters` (sync klient má přepsatelnou `STATSHUNTERS_BASE_URL`, takže jde testovat proti lokálnímu falešnému serveru).
6. **Drobnosti:** sjednotit duplicitní frontier logiku (`frontier.py` vs. `_frontier_tiles` ve `scoring.py`), konfigurovatelný typ aktivity, UI filtr top-N doporučení.
