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
```

Spouštět z kořene repozitáře. Frontend běží přímo na kořenové URL (`/`), API pod `/api/...`.

### Prostředí

- Python 3.13+ (vyvíjeno na 3.14), závislosti: `pip install -r requirements.txt`
  (fastapi, uvicorn, pyyaml, shapely)
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
statshunters:
  share_link: ""             # https://www.statshunters.com/share/<kod> nebo jen <kod>
```

Share link se vytváří na statshunters.com → Settings → Share link. Místo configu jde zadat
i proměnnou prostředí `STATSHUNTERS_SHARE_LINK` (má přednost — hodí se, když kód nechcete
mít v commitovaném `config.yaml`). `STATSHUNTERS_BASE_URL` přepíše adresu API (pro testy).

Pozn.: `home` a délka trasy se zatím používají jen informativně v `/api/summary` — plánování tras je ještě neimplementované (viz Další kroky).

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
| `src/geojson.py` | převod tiles na GeoJSON polygony |
| `src/web/` | Leaflet frontend (mapa, přepínání vrstev, statistiky, legenda, sync tlačítko) |

### Barvy na mapě

Každý navštívený tile se kreslí jednou, barvou podle **období poslední návštěvy** (studená → teplá):
modrá `#2a78d6` = naposledy před letoškem, žlutá `#eda100` = letos (před více než 3 měsíci),
červená `#e34948` = poslední 3 měsíce. Doporučené tiles: fialová `#4a3aa7` = nikdy nenavštívené,
akvamarín `#1baf7a` = návrat po čase; obojí s tmavým obrysem. Paleta je ověřená validátorem
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
| `POST /api/sync` | stáhne čerstvá data ze StatsHunters, přepíše `data/` a vyčistí cache |
| `GET /api/tiles`, `/api/frontier`, … | aliasy pro období `all` |

Všechny odpovědi se počítají při prvním dotazu a drží v `lru_cache` — po ruční změně dat je potřeba restart serveru; `POST /api/sync` si cache čistí sám.

## Jak se počítá skóre doporučených tiles

Kandidáti = hraniční tiles všech období + tiles nenavštívené letos / v posledních 3 měsících.
Skóre tile má dvě složky:

1. **Priority** — 9 pravidel (zvětšení square / clusteru / nenavštívenost × období celkem → letos → 3 měsíce), každé splněné přidá váhu `2^k`. Vyšší priorita vždy přebije všechny nižší dohromady.
2. **Stáří poslední návštěvy** — spojitý bonus 0–1: nikdy nenavštívený tile = 1,0; jinak `dny od poslední návštěvy / 1095` (strop 3 roky). Bonus je menší než minimální rozestup mezi kombinacemi priorit (2), takže jen doladí pořadí mezi tiles se stejnými prioritami — „kde jsem dlouho nebyl" vyhrává.

**Zásada (důležité pro budoucí plánování tras):** skóre tile je *čistý přínos* jeho návštěvy — nikdy nesmí obsahovat náklady na cestu (MHD, vzdálenost od domova). Optimalizace trasy bude maximalizovat součet přínosů tiles na trase; kdyby byla cena dopravy ve skóre, započítala by se tolikrát, kolika tiles trasa projde. Náklady na dopravu patří až na úroveň trasy, jednou za trasu.

## Stav vývoje (2026-07-18)

**Hotové (commitnuté):** mapa s tiles pro 3 časová období, obrysy max clusteru a max square, panel se statistikami, scoring doporučených tiles (`/api/opportunities`).

**Nové (zatím necommitnuté):**
- automatická synchronizace dat ze StatsHunters share API (`--sync`, `POST /api/sync`, tlačítko v mapě) — ověřeno proti lokálnímu falešnému API,
- bonus za stáří poslední návštěvy ve scoringu + zobrazení „Naposledy: datum (před N dny)" v popupu.

## Další kroky (návrh)

1. **Generování tras** — hlavní chybějící kus: z doporučených tiles sestavit reálnou okružní trasu o délce `target_distance_km ± tolerance` (start doma, nebo start/cíl u MHD). Možnosti: lokální OSM síť + `osmnx`/`networkx`, nebo externí router (BRouter, Valhalla, OSRM). Výstupem i GPX export do hodinek. Cílová funkce má dvě zásady:
   - přínos trasy = **společný přínos celé množiny tiles na trase**, ne součet individuálních skóre — strukturní zisky nejsou aditivní (skupina sousedních tiles může zvětšit square/cluster, i když žádný z nich samostatně ne). Metriky je potřeba přepočítat s celou množinou tiles trasy najednou; individuální skóre tiles slouží jen jako heuristika pro výběr kandidátů;
   - náklady na dopravu se odečtou **jednou za trasu** (viz zásada výše), nikdy per tile.
2. **Dosažitelnost** — filtr kandidátů podle doběhu/dojezdu MHD. Jen jako filtr nebo route-level náklad, ne jako složka skóre tile.
3. **Výkon `/api/opportunities`** — `_measure_gain` přepočítává celý cluster/square pro každého kandidáta; s růstem dat zvážit inkrementální výpočet a persistentní cache (teď jen `lru_cache` do restartu).
4. **Testy** — v repu zatím žádné; pytest pro `cluster`, `square`, `scoring`, `statshunters` (sync klient má přepsatelnou `STATSHUNTERS_BASE_URL`, takže jde testovat proti lokálnímu falešnému serveru).
5. **Drobnosti:** sjednotit duplicitní frontier logiku (`frontier.py` vs. `_frontier_tiles` ve `scoring.py`), konfigurovatelný typ aktivity, UI filtr top-N doporučení.
