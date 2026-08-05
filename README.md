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

**Cache prohlížeče.** Statické soubory se servírují s `Cache-Control: no-cache`
(`FreshStaticFiles` v `src/api.py`). Bez té hlavičky si prohlížeč podle HTTP heuristiky
(RFC 9111, 4.2.2) určí platnost sám — typicky z desetiny stáří souboru podle
`Last-Modified` — a novou verzi `app.js` si nemusí vyžádat **vůbec**; restart serveru s tím
nic neudělá, protože se ho prohlížeč neptá. Změny ve frontendu se pak tváří, jako by se
„nezveřejnily". `no-cache` neznamená „neukládej", ale „před použitím se zeptej", takže
`304 Not Modified` funguje dál a tělo souboru se zbytečně nepřenáší. Když se přesto zdá,
že UI drží starou verzi, pomůže jednorázově tvrdý reload (Ctrl+F5).

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
quiet_weight: 0.6            # 0 = jen sběr dlaždic, 1 = klid a značené trasy
mapy_cz:
  api_key: ""                # turistický podklad v mapě; bez klíče se použije OSM
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
| `src/geo.py` | geometrie na kouli, azimuty, čtení OSM tagů — nejnižší vrstva bez závislostí |
| `src/runcost.py` | **cenový model**: preference typů cest + kontext (podél čeho vede, značená trasa), měření délky po grafu |
| `src/waygraph.py` | pěší graf OSM: stažení z Overpass, tři vrstvy cache, obohacení (názvy ulic, značky), prostorové indexy |
| `src/routeplan.py` | výběr trasy: které dlaždice a v jakém pořadí, portfolio variant, cílová funkce, GPX, CLI |
| `src/itinerary.py` | itinerář běhu — tahák na trasu (kroky, kilometráž, odbočky, orientační body, rozcestí) |
| `src/transit.py` | síť MHD z PID GTFS + router spojení (min. přestupů, priorita druhů) |
| `src/expedition.py` | výpravy: cílové oblasti, spojení tam/zpět, časový rozpočet |
| `src/landmarks.py` | vodní toky a železnice (osmnx features) + geometrické křížení pro itinerář |
| `src/basemap.py` | podkladová mapa v UI — Mapy.cz turistická vrstva (API klíč), fallback na OSM |
| `src/pois.py` | orientační body a občerstvení z OSM (vyhlídky, studánky, pitná voda, restaurace) s odstupňováním podle přiblížení |
| `src/geojson.py` | převod tiles na GeoJSON polygony |
| `src/web/` | Leaflet frontend (mapa, přepínání vrstev, statistiky, legenda, sync tlačítko) |
| `tests/` | pytest — čisté funkce, cenový model, itinerář; `-m slow` měří kvalitu na reálném grafu |

### Podkladová mapa — Mapy.cz

Výchozí podklad je **turistická mapa Mapy.cz** (mapset `outdoor`), protože kreslí
**značené trasy a cyklotrasy** — tedy přesně to, podle čeho se trasa plánuje
(`RUN_PREFERENCES`, atribut `trail`) a podle čeho se pak běží. Na OSM podkladu vidět
nejsou, takže nešlo zkontrolovat, kudy naplánovaná trasa vlastně vede.

Dlaždice jdou přes oficiální REST API Mapy.com, které vyžaduje **API klíč**:

1. klíč zdarma na [developer.mapy.com](https://developer.mapy.com) (bezplatná kvóta
   pokryje osobní použití s přehledem),
2. vložit do `config.yaml` (`mapy_cz.api_key`) **nebo** nastavit `MAPY_CZ_API_KEY`
   (má přednost — hodí se, když klíč nechcete mít v commitovaném configu).

Bez klíče se nic nerozbije: `GET /api/basemap` vrátí `provider: "osm"` a mapa zůstane
na OpenStreetMap.

Přepínač podkladů je vlevo nahoře: **Turistická** (výchozí), Základní, Zimní a Letecká
(letecká se automaticky doplní vrstvou názvů `names-overlay`, bez ní je k orientaci
nepoužitelná). Retina dlaždice (`@2x`) API nabízí jen u `basic` a `outdoor`, jinde se
berou standardní. Zobrazení **loga Mapy.com** je podmínka používání jejich API, ne
dekorace — připíná a odepíná se spolu s jejich podkladem.

Klíč konzumuje prohlížeč, takže se posílá do frontendu — u klientských map běžný model;
na developer.mapy.com jde klíč omezit na konkrétní domény. Proxovat dlaždice přes backend
by šlo, ale pro lokální nástroj je to jen latence navíc.

**Metro jako vlastní vrstva.** Turistická mapa Mapy.cz trasy metra nekreslí a bez nich se
v mapě špatně orientuje. Data ale už máme — síť PID se načítá kvůli výpravám — takže se
z ní jen postaví linie (`metro_geometry`, `GET /api/transit/metro`, přepínač **Metro**
v panelu). Barvy jsou ty skutečné (A zelená, B žlutá, C červená), přestupní stanice bílé.
Každá stanice má v GTFS uzel pro každé nástupiště, proto se slučují **podle názvu** —
jinak by každá linka vyšla jako dvě rovnoběžky pár metrů od sebe. Vrstva leží pod
dlaždicemi: je to orientační podklad, nemá překrývat data.

**Orientační body a občerstvení taky vlastní vrstvou.** Mapy.cz je v dlaždicích nekreslí
a jejich API je nemá: `/v1/poi`, `/v1/places`, `/v1/pois` i `/v1/search` vrací **404** a
dokumentované funkce jsou jen dlaždice, geokódování, routing, výšky, statické obrázky a
časová pásma (ověřeno 07/2026; navíc stažená dlaždice z18 nad Karlovým náměstím ukazuje
popisky budov, ale žádné ikony amenit). Postupné objevování podle důležitosti, které má
jejich aplikace, je vlastnost jejich vektorových dlaždic — z veřejného API se získat nedá.

Zdrojem je proto **OSM přes Overpass** (`src/pois.py`, `GET /api/pois`, přepínač
**Orientacni body a obcerstveni**), s coverage cache jako u grafu. Odstupňování se dělá
samo: každý bod dostane `min_zoom` podle kategorie a významnosti (odkaz na
Wikipedii/Wikidata ho posune o jedno přiblížení dřív) a vrstva ukazuje jen to, co se do
daného zoomu hodí — jinak by Praha byla jedna kupa ikon. Naměřeno pro 10 km kolem Karlova
nám.: **5 643 bodů staženo za 12 s** (4 531 občerstvení, 389 památníků, 292 vyhlídek,
237× pitná voda, 70 vrcholů, 65 studánek).

| kategorie | od zoomu | | kategorie | od zoomu |
|---|---|---|---|---|
| vyhlídka, rozhledna, vrchol | 12 | | památník | 15 |
| hrad/zámek, studánka, pitná voda | 13 | | občerstvení | 16 |

Prahy vycházejí z hustoty: v běžném výřezu (1200 × 800 px) je při z13 vidět 556 bodů
(vyhlídky, voda, vrcholy — přesně to, co se hodí při pohledu na celou trasu), při z16 pak
336 restaurací. **Občerstvení je proto až od z16** — v centru Prahy je restaurací taková
hustota, že o zoom dřív by z nich byla souvislá plocha. Aby to nevypadalo, že tam žádné
nejsou, panel pod přepínačem vypisuje, co je ve výřezu vidět a kolik bodů čeká na
přiblížení.

Dvě pravidla, která dělají většinu užitku:

- **Pamětní destičky a kameny zmizelých se zahazují** (`SKIPPED_MEMORIALS`). V OSM jsou
  taky `historic=memorial`, ale jsou v dlažbě nebo na zdi a je jich řád — v okolí Karlova
  nám. **888 z 1 277** pojmenovaných „památníků". Bez toho filtru vrstvu úplně zaplavily
  a skutečné sochy v nich zanikly. Zbylo 389 soch, bust, válečných pomníků a zřícenin.
- **Bezejmenná restaurace se zahazuje** (je to šum), **bezejmenná studánka ne** — pro běžce
  pořád značí vodu.

Klasifikace má verzi (`POI_VERSION` v názvu souboru cache), takže se po její změně stará
data nepoužijí.

Pozn.: síť PID je **snímek** — `load_transit_graph()` použije `data/transit_graph.json`,
dokud sedí verze, a GTFS stahuje jen když zip chybí. Dočasně uzavřená stanice (07/2026
například Flora na trase A) v datech správně chybí, ale i po znovuotevření se sama
neobjeví — je potřeba smazat `data/pid_gtfs.zip` a `data/transit_graph.json`.

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
| `GET /api/basemap` | konfigurace podkladové mapy (Mapy.cz klíč + atribuce, jinak OSM) |
| `GET /api/transit/metro` | trasy metra a stanice pro orientaci v mapě (z už načtené sítě PID) |
| `GET /api/pois` | orientační body a občerstvení z OSM; `?lat=&lon=` (výchozí domov) |
| `GET /api/summary` | počty aktivit, config, metriky pro všechna období |
| `GET /api/periods/{period}/tiles` | navštívené tiles (GeoJSON); `period` = `all` \| `year` \| `recent` |
| `GET /api/periods/{period}/frontier` | hraniční tiles |
| `GET /api/periods/{period}/cluster` | obrys největšího clusteru |
| `GET /api/periods/{period}/square` | obrys největšího čtverce |
| `GET /api/opportunities` | doporučené tiles seřazené podle skóre (rank, důvody, přínosy, stáří poslední návštěvy) |
| `POST /api/route` | naplánuje okruh; JSON body `{lat, lon, distance_km, tolerance_km, quiet_weight}` (vše volitelné, výchozí z configu); vrací délku, waypointy, protnuté tiles, souřadnice, měrky kvality i GPX |
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
python src/routeplan.py --gpx route.gpx
python src/routeplan.py --lat 50.1030 --lon 14.4500 --distance 10 --tolerance 2
```

Jak to funguje:

1. **Pěší graf OSM** (`src/waygraph.py`) se stáhne z Overpass API kolem startu (poprvé
   jednotky minut) a cachuje do `data/walk_<lat>_<lon>_<reach>km.graphml`. Nad tím jsou
   ještě dvě vrstvy cache:
   - Cache graphml je podle **pokrytí**: použije se jakýkoli uložený graf, jehož kruh pokrývá
     požadovaný start + dosah (s tolerancí 1,5 km — dosah je horní odhad a chybějící vnější
     lem trasu nerozbije); stahuje se velkoryse (min. 10 km), takže změna délky ani startu
     v okolí nevyvolá nové stahování.
   - **Připravený graf** (názvy ulic, značené trasy, ceny hran) se ukládá jako
     `data/walk_*.prepared-<otisk>.pkl`. Parsování graphml trvá ~22 s a příprava dalších
     ~6 s; z pickle je to **~4 s** (7× rychleji, soubor 79 MB = polovina graphml). V názvu je
     **otisk parametrů**, které přípravu ovlivňují (preference cest, `ALONG_MAJOR_FACTOR`,
     `TRAIL_BONUS`, prahy párování ulic) — po jejich změně se stará cache nenajde a nová
     vznikne vedle; staré otisky téhož grafu se mažou. Bez toho by se po ladění preferencí
     tiše plánovalo podle starých cen. Zápis je atomický přes `.tmp`, poškozený soubor se
     zahodí a přepočítá.
   - Načtený graf zůstává v paměti serveru.
2. **Výběr trasy porovnáním variant**: staví se portfolio okruhů — rank-greedy seed
   (cheapest insertion na odhadech vzdušná čára × 1,35), seedy kolem **skupin
   sousedících kandidátů** (4-okolí) a **seedy na dokompletování square** (okna
   (side+1)² s ≤ 4 chybějícími tiles v dosahu — jednotlivé chybějící tiles mají
   samy o sobě nulový square přínos, proto je obecné hledání nemá důvod kombinovat).
   Každá varianta se exaktně přepočítá (`bidirectional_dijkstra` vážená
   **preferencemi typů cest**: cyklostezka 0,60 > pěšina/turistická cesta 0,70 >
   pěší zóna 0,80 > chodník 0,85 > klidná ulice 1,0; rušné silnice ×1,35–3,0 a
   schody ×1,4 penalizované — délková tolerance se ale vždy kontroluje proti
   skutečným metrům) upravenými o **kontext, ve kterém cesta vede** (viz níže)
   a ohodnotí **společným přínosem všech protnutých tiles**
   (`scoring.evaluate_tile_set`): Δsquare a Δcluster se počítají s celou množinou
   najednou (zisky nejsou aditivní), plus počty nových tiles podle období a
   staleness bonusy. Váhy: priorita (viz tabulka výše) × velikost zisku, přičemž **square se váží
   plochou** (side² − baseline²) — bez toho by snadný růst clusteru o pár tiles
   vždy přebil vzácný růst square a obrátil pořadí priorit. Vítěz se ještě zkouší
   vylepšit přidáváním nevyužitých kandidátů (2 kola). Při přetečení tolerance
   odpadá nejslabší waypoint; naopak pokud trasa nedosáhne spodní hranice, dotáhne
   se přes další tiles v dosahu (`_extend_to_window`).
3. **Cílová funkce** (`_variant_score`) — přínos postupně snižovaný třemi **podílovými**
   měrkami kvality:

   ```
   skóre = přínos × (1 − 0,5  × podíl opakovaných ulic)
                  × (1 − váha klidu × podíl délky podél významných ulic)
                  × (1 − váha klidu × 0,5 × podíl délky MIMO značené trasy)
                  × (1 − 0,35 × odchylka délky od cíle / tolerance)
   ```

   Podíly, ne absolutní hodnoty — s absolutní penalizací byl vždy nejvýhodnější
   nejkratší přípustný okruh. Násobení přínosem drží měřítko a nedovolí zápornou
   hodnotu; skóre nikdy nepřeroste přínos.
   - **Podíl podél významných ulic** — bez něj plánovač neuměl porovnat „hodně
     dlaždic po magistrále" s „míň dlaždic po klidu": vzal první a druhou variantu
     ani nepostavil.
   - **Podíl mimo značené trasy** — druhá strana téže preference. „Nevede podél
     magistrály" je jen nepřítomnost špatného; značka vede údolím, parkem nebo podél
     vody, a to je to, co trasu opravdu odliší. Bez tohoto členu posuvník narazil na
     strop: v portfoliu ležela trasa se **72 % délky po značkách** a prohrávala
     s trasou se 47 %, protože ji nic neodměňovalo. Penalizuje se podíl *mimo* značky,
     aby skóre nikdy nepřerostlo přínos.

   Váha obou je **runtime parametr** (posuvník *sběr dlaždic ↔ klid a značky*
   v panelu, `quiet_weight` v `POST /api/route`, výchozí z configu), protože je to
   preference, ne fyzika. Do portfolia navíc přibývají **klidné varianty**: tytéž
   cílové dlaždice, ale úsek se hledá s přirážkou cestám podél významných ulic a se
   slevou značeným trasám — dvě úrovně (`QUIET_LEG_PROFILES`), stejným vzorcem jako
   nízkoopakovací varianty. Naměřeno na okruhu 15 ± 3 km z Karlova nám.:

   | váha klidu | podél rušných | po značkách | délka | přínos | shoda s vahou 0 |
   |---|---|---|---|---|---|
   | 0,0–0,4 | 8,6 % | 54,2 % | 15,10 km | 118,7 | 100 % |
   | 0,6 | 1,5 % | 47,1 % | 14,93 km | 112,5 | 53 % |
   | 0,8–1,0 | 0,9 % | **78,6 %** | 16,19 km | 112,5 | **24 %** |

   Úrovně přirážky **nezávisí na posuvníku** — mají být pevnou sadou kandidátů, ze
   které si váha vybírá. Když přirážka škálovala s vahou, měl plánovač při každé váze
   jen jednu klidnou variantu; silnější přirážka dělala delší trasu, tu potrestala
   penalizace odchylky délky a vyhrála základní varianta, takže posuvník vycházel
   **nemonotónně** (váha 0,6 dala 8,6 % podél rušných ulic, zatímco 0,2 jen 0,9 %).
   - **Odchylka délky od cíle** — tolerance byla zavedena jen jako obálka
     splnitelnosti, ale bez tohoto členu se z ní stala preference: delší trasa protne
     víc dlaždic, takže vyhrávaly trasy u horní hranice. Teď je horní hranice pořád
     přípustná, jen ji trasa musí vyplatit vyšším přínosem. Doplňuje to
     `_shrink_toward_target` — zrcadlový protějšek `_extend_to_window`, který
     z příliš dlouhé trasy zkouší vypustit waypoint. Pracuje se **skutečnou** délkou:
     odhad (`_estimate_path_m`) systematicky podstřeluje, takže sekvenci naplněnou
     „jen po cíl" exaktní přepočet stejně vyžene nad cíl. Naměřeno: cíl 15 ± 3 km →
     14,93 km (2 % tolerance), cíl 10 ± 2 km → 11,45 → **10,84 km**.

   Cílová funkce **smí** delší trasu vybrat, když se vyplatí. U cíle 20 ± 4 km z Karlova
   nám. měla na výběr 19,97 km s přínosem 308 a vybrala 22,47 km s přínosem 410 — 33 %
   víc dlaždic za 12 % délky. Kdo chce cílovou délku držet tvrději, má na to
   `LENGTH_PENALTY_FRACTION`; zúžit toleranci teď taky funguje (15 ± 1 km dá tutéž
   trasu jako 15 ± 3 km).
4. **Kudy tile protnout**: waypoint není střed dlaždice, ale uzel uvnitř ní, který
   nejméně zajíždí z předchozího bodu k dalšímu cíli — trasa se tak dlaždice dotkne
   tam, kudy stejně vede. Kvůli chybě GPS/navigace se drží **rezerva 75 m od hranice**
   (`TILE_MARGIN_M`), takže trasa vede prokazatelně dovnitř; naměřená hloubka průniku
   na reálných trasách je 220–540 m. Odhady délky s tím počítají (waypoint má
   efektivní poloměr ~700 m) — bez té korekce nadhodnocovaly délku 1,5–4× a trasy
   vycházely zbytečně krátké.
5. **Ořez ocásků**: slepé úseky tam-a-zpět se zkracují na nejmenší délku, která
   zachová množinu protnutých tiles **i bezpečnou hloubku průniku**. Původně stačilo, aby
   dlaždici pokrýval jakýkoli jiný uzel trasy — jenže právě špička ocásku bývá to, kvůli
   čemu se do dlaždice zajíždělo, takže ořez nechal trasu, která ji jen škrábne. Naměřeno:
   cílová dlaždice měla v bezpečné zóně 2 362 uzlů (až 778 m hluboko), ale trasa jí prošla
   **nejhlouběji 49 m** — pod `TILE_MARGIN_M` (75 m), který má chránit proti chybě GPS.
   Uzel se teď nesmí odříznout, když je poslední dost hluboký ve svém tile. Po opravě:
   hloubka **49 → 98 m**, u sousední dlaždice 72 → 95 m, žádná mělká nezůstala; trasa se
   prodloužila o 120 m (14,93 → 15,05 km). Přínos se nemění, hluchá vzdálenost mizí.
   Navíc se **penalizuje průchod stejnou ulicí**: hrana už použitá na trase se při
   plánování dalšího úseku zdraží, takže se okruh vrací jinudy. Nízkoopakovací
   varianty se počítají rovnou v portfoliu (top 3 seedy, `AVOID_VARIANTS`), ne až
   jako oprava vítěze. Opakování je měkká složka cílové funkce: skóre = přínos ×
   (1 − 0,5 × **podíl** opakovaných metrů). Podíl, ne absolutní kilometry — s
   absolutní penalizací vycházel vždy nejlevněji nejkratší přípustný okruh.
   Míra vyhýbání je laditelná konstantou `REPEAT_PENALTY_FRACTION`.
6. **Kontext cesty, ne jen její typ** — typ sám o sobě nestačí: v pěším grafu je
   přes 80 % délky `footway`, takže chodník podél čtyřproudé silnice vypadal stejně
   dobře jako pěšina v parku, a protože je levnější než klidná ulice (0,85 vs. 1,0),
   trasy se na velké tahy přímo lepily. Chodníkům a cestám vedeným podél **významné
   ulice** (tertiary+; atribut `along_major` z `enrich_streets`) se proto preference
   odebírá (`ALONG_MAJOR_FACTOR` 1,25 — nikdy cestu nezlevní, jen zastropuje) a
   **značené trasy** dostávají bonus (`TRAIL_BONUS` 0,85). Ceny hran se proto počítají
   **až po** obohacení grafu (`_prepare`). Naměřeno na okruhu 15 ± 3 km z Karlova nám.:
   podíl délky podél významných ulic **62,9 % → 21,4 %**, klidné cesty 34,9 % → 67,6 %,
   po značených trasách 44,8 % → 64,5 %, při stejné délce. Cenou je nižší přínos
   (209 → 43, celý rozdíl leží ve 3měsíční vrstvě) — dokud kvalita cest není v cílové
   funkci výběru variant, plánovač tenhle kompromis nevolí vědomě (viz Další kroky).
7. **Itinerář běhu** (`route_directions` + `src/landmarks.py`) — tahák na trasu: úseky
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
   - **Jedna kilometráž pro celý itinerář**: vzdálenost ke krokům i k orientačním
     bodům se počítá ze **skutečné délky hran**, stejně jako délka trasy. (Dřív se
     orientační body měřily vzdušnou čarou mezi uzly; u klikatých cest je to jiné
     měřítko — obě stupnice se na referenčním okruhu rozešly o 340 m a křížení se
     hlásila ještě *před začátkem* svého úseku.) Kroky si drží indexy do trasy, ne
     uzly — uzel se může na okruhu opakovat, index určuje místo jednoznačně.
   - **Orientační body**: křížené významné ulice (tertiary+, kolmé ke směru) z ulic
     nad rámec grafu; a **vodní toky + železnice** (v pěším grafu nejsou) — vodní toky
     si nesou název (Botič, Vltava), tratě genericky „žel. trať". Křížení se hledá
     geometricky (shapely) a řadí podle vzdálenosti; ulice, po níž právě běžíme nebo
     hned poběžíme, se jako křížení neuvádí. Jestli je ulice **křížená, nebo jen
     souběžná**, se pozná ze směru, kterým trasa uzlem prochází (tětiva přes uzel) —
     ne z libovolného souseda uzlu v grafu, což hlásilo souběžné ulice jako křížené.
     Tatáž ulice se do 400 m (`CROSSING_DEDUP_M`) nehlásí znovu, a to **napříč kroky**:
     hranice úseku není důvod uvádět Legerovu dvakrát po sobě.
   - **Žádné prázdné pokyny**: změna názvu ulice bez zatočení není pokyn. Dřív se
     hlásila jako „rovne" a tvořila polovinu řádků itineráře.
   - **Rozhodovací body** — místa, kde se dá zabloudit, i když se nemění název cesty.
     Řádky vznikají změnou popisu úseku, takže zatáčka *uvnitř* úseku (nebo taková,
     jejíž krátký úsek pohltilo slučování) beze stopy mizela: na referenční trase mělo
     40 kroků jen 18 pokynů, přitom zatáček na křižovatkách je 134. Dvě věci to musely
     ustát:
     1. **směr se vyhlazuje přes ~50 m** — jednotlivá hrana v síti chodníků (přechod,
        obejití rohu) jinak vypadá jako zatáčka;
     2. **sousední detekce se shlukují** — uzly jsou po ~39 m, takže jednu zatáčku hlásí
        několik uzlů za sebou (i po vyhlazení jich zbylo 90).

     S obojím a s podmínkou, že z uzlu vede ještě jiná cesta (kde se nedá odbočit, není
     co splést), vychází **28 pokynů na 15 km (1,9/km)** — 18 u kroků a 10 uvnitř.
     Bod u hranice úseku se nehlásí dvakrát: patří pokynu kroku. Naopak když krok pokyn
     nemá a rozhodovací bod na jeho začátku je, pokyn se doplní — přechod na jinou cestu
     bez výrazného zatočení je právě to, co se snadno přejede.
   - **Odstupňování odboček**: 35–60° „mírně vlevo", 60–120° „vlevo", 120–150° „ostře
     vlevo", nad 150° „zpět". Dřív se mírný ohyb a vlásenka četly stejně.
   - **Slučování úseků nesmí spolknout ulici, po které se běží.** Krok vzniká změnou popisu
     a krátké kroky se slučují se sousedem — jenže popis přebíral **pohlcující** krok, takže
     se úsek jmenoval podle ulice, na kterou trasa teprve najede. Naměřeno na výpravě do
     Zbuzan: „mírně vlevo **Do Vršku**" ve skutečnosti znamenalo 162 m po Ořešské a teprve
     pak vpravo do Do Vršku; „vlevo **Mezi Lány**" začínalo 36 m po U Opatrovny; „vlevo
     **Plzeňská**" 188 m předtím. Kdo se tím řídí, hledá odbočku, která tam ještě není.
     Tři příčiny, všechny opravené:
     1. **Slučování nepřekročí rozhodovací bod** — jinak odbočka zmizí i s názvem ulice
        před ní. Proto se rozhodovací body hledají ještě před slučováním.
     2. **Krátká bezejmenná mezera se zaceluje** (`STEP_GAP_MAX_M`). Pět metrů bez názvu
        rozdělilo Ořešskou (163 m) na 81 + 77 m, obojí pod prahem — a slučování ji celou
        rozebralo do sousedů.
     3. **Prahy podle naměřeného rozložení**: pojmenovaný úsek se pohlcuje až pod **30 m**
        (dřív 100 m), nepojmenovaný pod **50 m** (dřív 250 m). Data z té výpravy ukazují
        čistou dělicí čáru — skutečné úseky mají 36–163 m (U Opatrovny, U Tyršovy školy,
        Ořešská), šum 4–9 m (Puchmajerova, Walterovo náměstí, Peroutkova); u bezejmenných
        jsou spojky do 43 m a pak skok na 70 m a výš — proto 50 m (85 m polní cesty před Mládkovou
        se při prahu 100 m pořád ztrácelo).

     Cena: itinerář se na 14,6 km rozrostl z 38 řádků na 56 — každý řádek ale nese ulici,
     po které se opravdu běží. Slučováním souvislé informace (níže) se pak vrátil na **40**
     (2,7 na km), tentokrát bez lží.
   - **Souvislá informace se neroztrhává.** Pravdivé popisky vyrobily opačnou vadu:
     jeden běh po žluté značce zabral sedm řádků podle názvů ulic, kterých si na značené
     cestě nikdo nevšimne. Tři pravidla, každé měřené na výpravě do Zbuzan:
     1. **Turistická značka je primární popis** — v ČR jsou značené trasy spolehlivě
        vyznačené v terénu, takže kdo běží po žluté, sleduje značky, ne cedule s názvy
        ulic. Navazující kroky po téže značce (`TRAIL_JOIN_SHARE`, 50 % délky kroku) se
        slučují a **ulice se přesunou do poznámky** („zluta turisticka (Pod Vavřincem,
        Mezi Lány, Radlická)", 1,15 km v jednom řádku místo sedmi). Cyklotrasy takhle
        popisovat nejde — značené jsou nespolehlivě, takže si nechávají názvy ulic.
        Značka **bez barvy** („turisticka znacka") se zahazuje úplně (`UNUSABLE_TRAILS`):
        jiné než barevné značky a naučné stezky u nás neexistují, generický popisek
        vzniká z relací bez `osmc:symbol` a jen mate (hlásil se podél Peroutkovy).
     2. **Chodník a pěšina podél téže ulice jsou jeden úsek** — mění se charakter cesty,
        ne kudy se běží (Novoveská 7,8–8,6 km z pěti řádků na jeden; Peroutkova 10,3–11,8 km).
     3. **Cesta, která se na chvíli přiblíží ulici, se nedělí** — souběžnost je náhodná.
        Aby se z ní ale nestal název celého úseku, sleduje se `own_m`: metry, kde hrana
        měla **vlastní** `name`. Krok, který ulici jen míjí, název nepřebírá (pěšina
        u Jitrocelové 5,6–6,5 km: pět řádků na dva, popis zůstal „pesina").
   - **Čas** se u kroku uvádí **kumulativně** od startu (podle tempa z panelu), stejně
     jako kilometráž — v jedné stupnici se to čte líp než směs „od startu" a „na tomhle
     úseku".
   - **Sběr dlaždic** — kvůli čemu se celý běh dělá. U kroku, kde trasa do cílové
     dlaždice vjede, se uvádí která, od kolika km, kolik kilometrů je uvnitř a
     **jak hluboko od hranice** se dostane. Hloubka rozhoduje, jestli se návštěva
     započítá i při chybě GPS; pod `TILE_MARGIN_M` (75 m) panel varuje „jen těsně!".
     Počítá se přes celou trasu najednou, ne po krocích — dlaždice přetékající přes
     několik úseků tak dá jeden záznam se skutečnou hloubkou, ne tři útržky; a každý
     sběr patří právě jednomu kroku.
     Měří se **po souřadnicích trasy, ne po uzlech grafu**. Uzly jsou rozestoupené
     desítky metrů, takže vjezd vycházel pozdě a délka uvnitř kratší, než je (naměřeno:
     hlášeno „od 7,29 km, 0,22 km uvnitř", ve skutečnosti od 7,24 km a 0,27 km) — a
     protože 7,29 přeteklo konec svého kroku, vypadalo i přiřazení špatně. Souřadnice
     navíc kopírují geometrii hran, tedy tytéž body, ze kterých se počítá
     `tiles_crossed` a z něj přínos trasy, takže si obojí nemůže odporovat.
   - **Klikatelné řádky**: kliknutí na řádek zvýrazní odpovídající úsek v mapě a
     přiblíží se k němu, druhý klik zvýraznění zruší. Souřadnice kroků se neposílají —
     dopočítají se v prohlížeči z kumulativní vzdálenosti, kterou už počítá odečet
     vzdálenosti po trase.
   - **Značka platí jen tam, kde opravdu vede.** Značená trasa se ke kroku připisovala,
     když pokrývala aspoň 30 % jeho délky — takže úsek, kde zelená v polovině odbočí jinam,
     byl celý označený jako zelená. To je nebezpečné: běžec se řídí značkami a odbočí s nimi.
     Naměřeno na výpravě do Zbuzan: zelená pokrývala 295 m ze 670metrového úseku (44 %).
     Když značka nepokrývá aspoň 90 % kroku, uvádí se **úsek, po kterém vede**
     („zelena turisticka jen 4.9–5.2 km"). Dělit kroky podle značky nešlo — 26 z 54 kroků
     má značek víc (cyklotrasy se překrývají) a itinerář by se zdvojnásobil.
     Rozcestí se pak hlásí až **za** místem, kde značka odbočí pryč; do té doby vede běžce
     ona. A shlukují se stejně jako odbočky, jinak jedno rozcestí hlásí několik uzlů za
     sebou (na jednom úseku jich vyskočilo sedm).
   - **Značené trasy**: turistické a cyklotrasy jsou v OSM **relace**, které osmnx
     features nevrací — stahují se přímo z Overpass (`build_trails`, 536 relací /
     21 tis. úseků pro Prahu za ~7 s) a přiřazují hranám stejným mechanismem jako
     ulice (atribut `trail`). V itineráři se uvádí barva značky („cervena
     turisticka") nebo číslo cyklotrasy („cyklotrasa A22").
     **Neznačené cyklotrasy se zahazují** (`UNSIGNED_ROUTE_STATES`). Pražská síť je
     v OSM vedena dvojmo: vedle relace skutečné trasy stojí relace návrhu, rozlišené
     tagem `state` — `proposed` je plánovaná, `recommended` doporučená
     (tj. doporučení cyklokoordinátora, **v terénu neznačené**). Bez filtru posílal
     itinerář běžce po značení, které neexistuje. Naměřeno na 405 cyklorelacích
     v okolí Prahy: 38 `proposed`, 232 `recommended`, 135 skutečných. Všech 100 tras
     s prefixem **X** („klidová alternativa"; X13 se jmenuje doslova „Klidová
     alternativa bud. A13") je `recommended`, stejně jako A135 nebo A235; u 53 čísel
     filtr nechá skutečnou verzi a zahodí jen dvojče „návrh" (A1, A13, A120).
     `complete=no` se nezahazuje — to je díra v trase, ne chybějící značení.
     Turistických tras se filtr netýká: `state` nepoužívají vůbec (113 ze 113 relací)
     a v ČR jsou značené spolehlivě — proto se dají použít jako primární popis.
     Dopad na datech (10 km kolem centra): cyklo úseky **24 941 → 15 412**,
     tras **252 → 106**, turistických úseků beze změny (6 082 → 6 085). U A13
     zbylo 11 úseků ze 166 — přesně ta „odbočka na Výtoni", kterou OSM uvádí
     v poznámce jako jediný vyznačený kus. Trasa se tím i posunula (14,59 → 14,66 km):
     značka zlevňuje hrany, takže plánovač předtím trasu stáčel na neexistující
     cyklostezky.
   - **Dotaz je bbox, ne `around:`.** Hledání relací podle vzdálenosti je pro Overpass
     řádově dražší — na 12 km kolem Prahy vracela **všechna** zrcadla 504, týž dotaz
     přes bbox doběhne za 7 s. Čtverec je nadmnožina kruhu a značky se přiřazují
     geometricky, takže přebytek nevadí.
   - **Selhání zdroje se nikdy nezapamatuje.** Dřív se výjimka spolkla (`except
     Exception: pass`) a do cache se zapsal prázdný seznam — jedno 504 od Overpassu
     tím připravilo celou oblast o značené trasy **natrvalo**, a protože značka
     zlevňuje hrany, měnilo to i navrhované trasy. Chyba stahování má teď vlastní typ
     (`SourceUnavailable`), plánování pokračuje bez toho zdroje, **vypíše varování
     na stderr** a neuloží nic — ani na disk, ani do paměti procesu. Totéž o patro
     výš: degradovaný graf se neukládá do pickle cache (`_prepare` vrací i příznak
     úplnosti). Raději pomalá příprava pokaždé než tiše horší výsledky. „V okolí nic
     není" se od výpadku odlišuje: osmnx to hlásí `InsufficientResponseError` a to je
     platný výsledek, který se cachovat smí.
   - **Rozcestí**: na **neznačených polních cestách a pěšinách** se hlásí každé
     rozcestí s kilometráží a pokynem „drž se vlevo/vpravo" (tam hrozí navigační
     chyba). Na chodnících v zástavbě se nehlásí (byly by desítky na kilometr)
     a na značených trasách taky ne — tam se orientuje podle značek.
   - Zdroje (ulice, bariéry) se cachují coverage cache jako graf; první stažení pro
     Prahu je v řádu minut (geometrie Vltavy, 25 tis. ulic), pak 0,1 s. Krátké úseky
     se slučují (bezejmenné pod 250 m, pojmenované pod 100 m). V panelu pod
     rozbalovacím „Itinerar behu".
   - **Odečet vzdálenosti v mapě**: najetím na trasu se ukáže, kolik km je daný bod
     od startu běhu a kolik zbývá (jako v mapy.cz), s kroužkem na nejbližším bodě
     trasy. Slouží k přesnému popisu míst („nepřesnost v 3,2 km"). Počítá se v
     prohlížeči z bodů trasy — souhlasí s délkou i itinerářem na jednotky metrů.
8. **Výběr z variant**: plánovač nevrací jen vítěze, ale **až 3 varianty k výběru**
   (`MAX_VARIANTS`). Portfolio jich porovná kolem dvanácti, jenže většina jsou přepočty
   *téže* sekvence (vyhýbání opakovaným ulicím, klidné varianty) lišící se o pár set
   metrů — nabízet je všechny by nedávalo smysl. Vybírají se proto jen ty, které vedou
   doopravdy jinudy: měří se **podíl společných hran** a varianta se zahodí, když jich
   s některou už vybranou sdílí přes `MAX_VARIANT_OVERLAP` (60 %). Naměřeno na okruhu
   15 ± 3 km z Karlova nám. — tři varianty se stejným přínosem 112,5, ale jiným
   charakterem (47 % / 79 % / 63 % po značkách, 1,5 % / 0,9 % / 13,2 % podél rušných),
   sdílející s vítězem 44 % a 60 % bodů. Každá je **kompletní** včetně itineráře a GPX,
   takže přepnutí v panelu nevyžaduje další dotaz na server. Varianty se nehodnotí —
   jen popisují; poslední slovo má uživatel (v horku se hodí jiná trasa než v zimě).
   Cena: skládání itineráře pro tři trasy místo jedné, plánování 7 → 15 s.
9. **Výstup**: délka, waypoint tiles, všechny protnuté tiles (počítané z plné
   geometrie hran — trasy v mapě i GPX kopírují skutečné tvary ulic), rozpad
   přínosu, itinerář, počet porovnaných variant, GPX. Navíc **měrky, podle kterých
   se trasa vybrala** (`along_major_km/_share`, `trail_km/_share`, `repeated_km`,
   `quiet_weight`, výsledné `score`) — panel je ukazuje pod přínosem, jinak by
   nešlo poznat, co posuvník udělal.

Naměřeno (Praha, okolí Karlova náměstí, graf 141 810 uzlů / 397 646 hran): plánování okruhu
**0,1–10 s** podle délky a počtu variant (15±3 km ≈ 6–10 s, kratší okruhy pod 1 s);
jednorázově načtení grafu **~4 s z pickle cache** (dřív 22 s parsování graphml + 6 s příprava)
a scoring ~9 s — obojí si server podrží v paměti.


## Testy

```bash
pip install -r requirements-dev.txt
pytest                 # rychlé testy (< 1 s) — běží při každé změně
pytest -m slow         # kontrolní měření na skutečném grafu Prahy (~1 min)
```

| Soubor | Co hlídá |
|---|---|
| `tests/test_metrics.py` | max square a max cluster (4-sousednost, díry, prázdná množina) |
| `tests/test_scoring.py` | pořadí vah priorit, neaditivita zisků nad množinou, square vážený plochou, strop staleness |
| `tests/test_cost_model.py` | pořadí preferencí typů cest + kontext: chodník podél rušné ulice prohrává s klidnou ulicí, značka je bonus, kontext hranu nikdy nezlevní |
| `tests/test_itinerary.py` | kilometráž kroků i orientačních bodů, souběžná ulice není křížení, deduplikace napříč kroky, žádné „rovne"; sběr dlaždic (hloubka průniku, každý sběr právě jednou), odstupňování odboček, rozhodovací body; slučování nesmí spolknout ulici (zacelení mezer, prahy úseků), rozsah platnosti značky |
| `tests/test_route_quality.py` | *(slow)* podíl délky podél významných ulic a klidných cest, dodržení tolerance, konzistence kilometráže na reálné trase |
| `tests/test_basemap.py` | zdroj API klíče (env > config), fallback na OSM bez klíče, placeholdery v URL dlaždic |
| `tests/test_trim_spurs.py` | ořez ocásků: hluboká špička přežije, mělká se ořízne, trasa zůstane souvislá |
| `tests/test_metro.py` | vrstva metra: sloučení nástupišť podle názvu, oba směry jako jeden úsek, přestupní stanice, barvy linek |
| `tests/test_pois.py` | klasifikace OSM tagů do kategorií, odstupňování podle přiblížení, zahození bezejmenných restaurací, deduplikace |
| `tests/test_static_cache.py` | statické soubory nesou `Cache-Control: no-cache` a zároveň validátory pro 304 |
| `tests/test_expedition.py` | časové okno běhu (doběh a jízda ho zkracují, 24minutový strop na spojení), jednosměrný tvar dá širší okno, práh pro blízké cíle, odhad sklizně |
| `tests/test_graph_cache.py` | cache připraveného grafu: zneplatnění při změně parametrů, round-trip, úklid starých otisků, odolnost proti poškozenému souboru |
| `tests/test_trails.py` | značené trasy: plánovaná i „doporučená" cyklotrasa se zahodí, existující přežije i s dírou (`complete=no`), turistických se filtr netýká; selhání stahování se nezapamatuje (disk ani paměť) a zkusí se všechna zrcadla |
| `tests/test_objective.py` | cílová funkce: skóre nikdy nepřeroste přínos ani nespadne pod nulu, symetrie penalizace délky, váha klidu i značené trasy umí přehodit vítěze; výběr variant (vítěz první, skoro stejné se sloučí) |

Rychlé testy běží nad **ručně postavenými grafy** (`line_graph` fixture v `conftest.py`) —
délky hran se zadávají nezávisle na vzdálenosti uzlů, protože v OSM `length` kopíruje
geometrii ulice, kdežto uzly jsou jen křižovatky. Právě na tom rozdílu se poznají chyby
kilometráže, a na grafu o sedmi uzlech je to vidět okamžitě.

Testy označené `slow` potřebují stažený pěší graf v `data/` a data aktivit; když chybí,
samy se přeskočí. Prahy v nich (`MAX_ALONG_MAJOR_PCT`) jsou **naměřené hodnoty s rezervou** —
jejich smysl je zachytit regresi cenového modelu, ne zabetonovat konkrétní trasu.

## Výpravy s MHD

Výprava má dva tvary. **Jednosměrná** (běh na zastávku → MHD → **běh domů**) je výhodnější
kdykoli se domů doběhnout dá: ušetří celou jednu jízdu a ten čas se přelije do běhu.
Zpáteční jízda totiž nic nepřináší — jen se platí. Naměřeno z Karlova nám. při 15 ± 3 km
a 120 min:

| tvar | přínos | běh | čas |
|---|---|---|---|
| čistý okruh bez MHD | 112,5 | 14,93 km | 89,6 min *(30 min nevyužito)* |
| MHD tam i zpět (okruh u cíle) | 124,8 | 12,64 km | 111,7 min |
| **jednosměrná: metro A na Motol, běh domů** | **307,7** | 16,49 km | 118,1 min |

I výprava se nabízí **v několika variantách** (`MAX_EXPEDITION_VARIANTS`) — různé cílové
zastávky a tvary, každá kompletní i s během, itinerářem a GPX. Rozlišují se podle dvojice
(tvar, výstupní zastávka); vnitřní varianty samotného běhu se zahazují, dvouúrovňový výběr
(kam jet a kudy běžet) by byl matoucí.

Okruh se zpáteční jízdou se počítá až jako náhrada pro cíle, ze kterých se domů doběhnout
nedá (`ONEWAY_HOME_SHARE`) — plánovat oba tvary pro každého kandidáta by dobu plánování
zdvojnásobilo. Jednosměrný tvar navíc obejde i 24minutový strop na spojení: platí se
jen jednou, takže projdou i cíle, na které okruh nemá čas (Zbuzany dřív „nevejde se do
rozpočtu", jednosměrně přínos 307,4).

Původní tvar = [běh na zastávku] → MHD → **běh** → MHD → [běh domů], s rozpočtem na celkový
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
   nezachytily). Předfiltr dosažitelnosti vyřadí ty, ke kterým se v rozpočtu nedá dojet.

   **Blízké oblasti se z MHD nevyřazují** (`MIN_TRANSIT_TARGET_SHARE`). Dřív platilo
   „bližší než polovina doběhu → tam si doběhneš sám", což byla chybná úvaha: doběhnout
   k oblasti 6 km daleko a zpět spotřebuje 12 z 15 km rozpočtu a na sbírání v oblasti
   nezbude nic. Naměřeno z Karlova nám.: Strašnická (5,5 km), Skalka (6,3 km) i Depo
   Hostivař (7,4 km) ležely pod starým prahem 8,1 km, přitom metrem A jsou za 9–16 minut
   a celá výprava vyjde na 105–112 minut ze 120. Po uvolnění prahu vzrostl počet
   použitelných kandidátů z **1 na 4** a vítězem se stala výprava metrem A na Zahradní
   Město (přínos 124,8 proti 112,5 čistého okruhu).

   Kandidáti se řadí podle **odhadu sklizně** (`_harvest_estimate`) — kolik přínosu
   posbírá okruh dané délky z místa výstupu — místo podle přínosu celé cílové oblasti.
   Ten byl zavádějící v obou směrech: velká oblast má vysoký součet, ale běh z ní stihne
   jen kousek (Zbuzany: odhad oblasti 1 229, skutečná trasa nic), kdežto blízká oblast má
   součet malý, i když by z ní běh nasbíral dost. **Pozor, odhad není zkalibrovaný**:
   proti třem skutečným trasám cíle MHD nadhodnocuje (Motol 299 → 6,3) a u vítězné
   varianty naopak podstřelil (94 → 124,8). Slouží jen k hrubému seřazení a proto se
   exaktně plánuje **5** kandidátů, ne 3 — kandidáti s už staženým grafem jsou levní. Pro top oblasti se najde spojení
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
- plánování okruhů (`src/routeplan.py` + `POST /api/route` + UI v mapě) — špendlík startu, délka/tolerance v panelu, vykreslení trasy, GPX ke stažení; po warm-upu přeplánování 0,3–1,7 s,
- **optimalizace společného přínosu**: trasa se vybírá porovnáním variant podle skutečného zisku statistik celé množiny protnutých tiles; square vážený plochou + seedy na dokompletování square (ověřený postup zlepšení z Karlova nám.: greedy 18,6 → portfolio 36,9 → square-aware 96,6, trasa 4×4 → 5×5 přes Košíře); rozpad přínosu se zobrazuje v panelu,
- **kvalita tras**: preference typů cest pro běh (cyklostezka > turistická cesta > park/pěší zóna > chodník > klidná ulice; rušné silnice penalizované) — trasa Karlovo nám. → Podolí přešla z 77 % preferovaných / 21 % silnic na 98 % preferovaných / 1 % silnic při stejné délce; trasy a GPX nově kopírují skutečné geometrie ulic (896 bodů místo ~370 uzlů).
  **Pozor na tuhle metriku**: počítá `footway` jako „preferovanou cestu" bez ohledu na to, podél čeho vede, takže vypadala výborně i pro okruh, kterému 63 % délky vedlo po chodnících podél magistrál. Skutečnou kvalitu měří až podíl délky **podél významných ulic** (viz bod 5 v „Plánování tras" a `tests/test_route_quality.py`),
- **kvalita okruhů (07/2026)**: waypointy míří dovnitř dlaždice místo do jejího středu (rezerva 75 m proti chybě GPS), penalizace opakování je podílová a nízkoopakovací varianty soutěží rovnou v portfoliu. Na výchozím okruhu z Karlova nám.: opakování **24 % → 3 %**, zamotaná Trója zmizela (**40 % → 0 %** bodů trasy v oblasti) a přínos vzrostl **90 → 209**. Kalibrace odhadů délky (waypoint má efektivní poloměr) navíc odstranila systematicky krátké trasy — všech 7 testovaných scénářů je nyní v toleranci (dřív 2 mimo),
- **výpravy s MHD** (`src/transit.py` + `src/expedition.py` + `POST /api/expedition` + tlačítko v UI) — ověřeno E2E: z Karlova nám. při 15±3 km / 120 min vyhrála výprava metro A + bus 350 do Roztok (benefit 1371 vs. 96,6 čistého okruhu, 3 nové tiles, 117,7 min); při 12±3 km / 150 min vlak T7 do Dobřichovic (0 přestupů) s dokompletováním **celkového square 15×15 → 16×16** (benefit 17 138, 148,4 min) — shoduje se s intuicí uživatele (Černošice/Solopisky), která na 120 min opravdu nevychází.

**Empirické zjištění (07/2026):** v doběhovém dosahu z Karlova náměstí (~8 km) je už všechno
navštívené i letos — lokálně jde zlepšovat jen 3měsíční metriky. Velké zisky (nové tiles,
celkový square/cluster) leží na okrajích navštíveného území, tj. vyžadují jiný start nebo
dopravu — to dává prioritu bodu „Dosažitelnost MHD" níže.

## Další kroky (návrh)

Pořadí vychází z revize kvality výstupů (07/2026). Hotová je vrstva P0 — kontext cesty
v cenách hran a čtyři vady itineráře (viz „Plánování tras", body 5 a 6).

**Nejbližší (P1) — jádro kvality výstupů:**

1. ~~Kvalita cest do cílové funkce.~~ **Hotovo (07/2026):** `_variant_score` má člen za podíl
   délky podél významných ulic i za odchylku délky od cíle, do portfolia přibyly klidné
   varianty a váha klidu je posuvník v panelu (`quiet_weight`). Viz „Cílová funkce" výše.
   Zbývá případně: vystavit `LENGTH_PENALTY_FRACTION` a `QUIET_LEG_FACTOR` do `config.yaml`,
   jestli se ukáže potřeba je ladit jinde než v kódu.
2. **Opakování koridoru, ne jen hrany.** `repeated_m` počítá druhý průchod **toutéž**
   hranou. Trasa, která jde údolím tam po jedné straně a zpět po druhé (nebo po
   souběžné pěšině), má opakování nulové a přitom vypadá jako pořád totéž — a při
   vysoké váze klidu, kde je na výběr málo cest, se to děje. Naměřeno při váze 1,0:
   opakování 0–1 % (Karlovo nám. i Zahradní Město), takže současná metrika ten jev
   nezachytí. Chtělo by to měřit blízkost trasy k sobě samé (např. podíl délky, která
   vede do X metrů od dřívějšího úseku), a teprve to dát do cílové funkce.
3. ~~Nabídnout několik dobrých variant, ne jen nejlepší.~~ **Hotovo (07/2026):** viz
   „Výběr z variant" níže. Zbývá případně měrka stínu (`natural=wood`, `landuse=forest`)
   a vody, aby šlo varianty popsat i podle nich — zatím se popisují délkou, přínosem,
   podílem po značkách a podél rušných ulic a rozhodnutí je na uživateli.
4. ~~Itinerář na rozhodovacích bodech.~~ **Hotovo (07/2026):** viz „Itinerář běhu" výše —
   rozhodovací body uvnitř úseků (18 → 28 pokynů), odstupňování odboček a kumulativní čas.
   Zbývá případně to, co jsem si u tohoto bodu původně sliboval a nedodal: **průběžné
   potvrzovací záchytné body** („po 2 km stále po značce, minul jsi rybník") pro dlouhé
   úseky bez pokynu.
5. ~~Dlaždice v itineráři.~~ **Hotovo (07/2026):** viz „Itinerář běhu" výše. Odhalilo to
   dva defekty, oba opravené: ořez slepých ocásků ubíral hloubku průniku do dlaždice
   (viz bod 4 v „Plánování tras") a sběr se měřil po uzlech místo po geometrii trasy.
6. ~~Popisky, které nelžou.~~ **Hotovo (08/2026):** viz „Itinerář běhu" výše. Krok se
   nejmenuje podle ulice, na kterou trasa teprve najede; značka platí jen tam, kde vede;
   souběžná ulice název nepřebírá (`own_m`) a souvislá informace se neroztrhává.
   Ověřeno proti reálu na výpravě do Zbuzan — uživatel prošel itinerář krok po kroku a
   žádný pokyn už nesvádí na špatnou cestu.
   Zbývá drobnost: značka končící uprostřed kroku se hlásí rozsahem („zelena turisticka
   jen 4,9–5,2 km") místo toho, aby krok rozdělila v místě, kde odbočí pryč.
7. ~~Rozdělit `src/routing.py` a přidat cache připraveného grafu.~~ **Hotovo (07/2026):**
   1 254řádkový `routing.py` je rozdělený na `geo` / `runcost` / `waygraph` / `routeplan` /
   `itinerary` (viz tabulka struktury) a připravený graf se cachuje do pickle. Ověřeno
   head-to-head proti verzi před refaktorem na témž grafu a týchž datech: shodná délka,
   přínos, waypointy, souřadnice i celý itinerář včetně křížení a rozcestí. `pytest -m slow`
   spadl z 57 s na 21 s.

**Dále (P2) — nová data:**

7. **Převýšení** (SRTM raster lokálně nebo OpenTopoData) — pro Prahu zásadní veličina, dnes
   chybí úplně: ani v ceně hran, ani v itineráři.
8. **Rozšířit `useful_tags_way`** o `surface`, `footway`, `tracktype`, `lit`, `incline`,
   `sidewalk` — bez `surface` nejde odlišit asfalt od bláta, bez `footway=crossing` přechod
   přes magistrálu. Pozor: vynutí to nové stažení všech grafů a zneplatní `data/*.graphml`.
   (V grafu naopak už *jsou* a nepoužívají se `maxspeed` a `lanes` — dobré proxy pro provoz.)
9. **Parky a lesy** jako polygony (`leisure=park`, `landuse=forest`) — dnes je běh parkem
   k nerozeznání od chodníku vedle něj.

**Průběžně:**

10. **Asynchronní příprava nové oblasti** — `POST /api/route` v úplně nové oblasti blokuje na
   minuty (Overpass download) a hrozí timeout prohlížeče; převést na úlohu na pozadí.
11. **Výpravy s MHD — další iterace**: nesymetrický návrat, reálné intervaly linek místo
    paušálního čekání (GTFS frequencies), přesnost pěších přesunů (teď vzdušná čára × 1,3).
    Náklady na dopravu zůstávají route-level, nikdy ve skóre tile.
12. **Výkon `/api/opportunities`** — `_measure_gain` přepočítává celý cluster/square pro každého
    kandidáta; s růstem dat zvážit inkrementální výpočet a persistentní cache.
13. **Testy dál**: `statshunters` sync klient (má přepsatelnou `STATSHUNTERS_BASE_URL`, jde
    testovat proti lokálnímu falešnému serveru), `transit`/`expedition` (dnes nepokryté).
14. **Drobnosti:** sjednotit duplicitní frontier logiku (`frontier.py` vs. `_frontier_tiles` ve
    `scoring.py`), konfigurovatelný typ aktivity, UI filtr top-N doporučení.
