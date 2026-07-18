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

## Konfigurace — `config.yaml`

```yaml
home:                  # výchozí bod tras
  name: Karlovo namesti
  lat: 50.0757
  lon: 14.4188
target_distance_km: 15       # cílová délka trasy
distance_tolerance_km: 3     # tolerance ±
```

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
| `src/scoring.py` | bodování kandidátních tiles podle 9 priorit (square/cluster/nenavštívený × 3 období) |
| `src/geojson.py` | převod tiles na GeoJSON polygony |
| `src/web/` | Leaflet frontend (mapa, přepínání vrstev, statistiky) |

## API

| Endpoint | Popis |
|---|---|
| `GET /api/health` | liveness check |
| `GET /api/summary` | počty aktivit, config, metriky pro všechna období |
| `GET /api/periods/{period}/tiles` | navštívené tiles (GeoJSON); `period` = `all` \| `year` \| `recent` |
| `GET /api/periods/{period}/frontier` | hraniční tiles |
| `GET /api/periods/{period}/cluster` | obrys největšího clusteru |
| `GET /api/periods/{period}/square` | obrys největšího čtverce |
| `GET /api/opportunities` | doporučené tiles seřazené podle skóre (rank, důvody, přínosy) |
| `GET /api/tiles`, `/api/frontier`, … | aliasy pro období `all` |

Všechny odpovědi se počítají při prvním dotazu a drží v `lru_cache` — po změně dat je potřeba restart serveru.

## Stav vývoje (2026-07-18)

**Hotové (commitnuté):** mapa s tiles pro 3 časová období, obrysy max clusteru a max square, panel se statistikami, přepínání vrstev.

**Rozpracované (zatím necommitnuté):** `src/scoring.py` + endpoint `/api/opportunities` + mapová vrstva „Doporučené tiles". Funkční — ověřeno, vrací ~1 570 doporučení s rankem, skóre a důvody. Scoring prochází kandidáty (hraniční tiles + tiles nenavštívené v kratších obdobích) a boduje je podle 9 priorit: zvětšení square / clusteru / nenavštívenost, vždy pro období celkem → letos → poslední 3 měsíce.

## Další kroky (návrh)

1. **Commitnout scoring** — funkce je hotová a ověřená.
2. **Stáří poslední návštěvy ve scoringu** — cíl „běhat, kde jsem dlouho nebyl" zatím pokrývají jen binární množiny za období. Tile databáze už ale `last_visit` obsahuje, jen ji scoring nevyužívá. Přidat spojitou složku skóre rostoucí se stářím poslední návštěvy.
3. **Generování tras** — hlavní chybějící kus: z doporučených tiles sestavit reálnou okružní trasu z `home` o délce `target_distance_km ± tolerance`. Možnosti: lokální OSM síť + `osmnx`/`networkx`, nebo externí router (BRouter, Valhalla, OSRM). Výstupem by měl být i GPX export do hodinek.
4. **Vzdálenost od domova** — filtrovat/penalizovat doporučení mimo doběhový dosah (config `home` k tomu už existuje).
5. **Automatická aktualizace dat** — stahovat aktivity ze StatsHunters share-link API místo ručních JSON souborů (`meta.limit: 500` napovídá stránkování po 500).
6. **Výkon `/api/opportunities`** — `_measure_gain` přepočítává celý cluster/square pro každého kandidáta; s růstem dat zvážit inkrementální výpočet a persistentní cache (teď jen `lru_cache` do restartu).
7. **Testy** — zatím žádné; pytest pro `cluster`, `square`, `scoring` (mají čistá vstup/výstup rozhraní).
8. **Drobnosti:** sjednotit duplicitní frontier logiku (`frontier.py` vs. `_frontier_tiles` ve `scoring.py`), konfigurovatelný typ aktivity, UI filtr top-N doporučení.
