"""Orientacni body a obcerstveni z OSM.

Turisticka mapa Mapy.cz je nekresli a jejich REST API je nema: /v1/poi, /v1/places
i /v1/search vraci 404 a dokumentovane funkce jsou jen dlazdice, geokodovani,
routing, vysky, staticke obrazky a casova pasma (overeno 07/2026). Postupne
objevovani podle dulezitosti, ktere ma jejich aplikace, je vlastnost jejich
vektorovych dlazdic - z verejneho API se ziskat neda.

Napodobuje se tedy tady: kazdy bod dostane `min_zoom` podle kategorie a
vyznamnosti a vrstva v mape ukazuje jen to, co se do daneho priblizeni hodi.
Bez toho by Praha byla pri oddaleni jedna velka kupa ikon.

Data se stahuji z Overpass a cachuji stejne velkoryse jako pesi graf (pokryti +
rezerva), takze druhy dotaz v okoli uz je z disku.
"""
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POI_DIR = ROOT / "data"
COVER_SLACK_KM = 1.5
MIN_DOWNLOAD_REACH_KM = 10
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

EARTH_RADIUS_M = 6371000.0

# Kategorie -> (OSM filtr, vychozi min_zoom, popisek, znacka).
#
# min_zoom je odstupnovani podle toho, jak moc je bod vzacny a jak daleko je
# videt: rozhledna nebo vrchol slouzi k orientaci uz z dalky, restaurace az kdyz
# se clovek divá na konkretni ulici. Studanky a pitna voda maji nizky prah
# zamerne - pro bezce je to provozni informace, ne zajimavost.
CATEGORIES = [
    ("viewpoint", '["tourism"="viewpoint"]', 12, "vyhlidka", "👁"),
    ("tower", '["man_made"="tower"]["tower:type"="observation"]', 12, "rozhledna", "🗼"),
    ("peak", '["natural"="peak"]', 12, "vrchol", "⛰"),
    ("castle", '["historic"~"^(castle|fort)$"]', 13, "hrad/zamek", "🏰"),
    ("spring", '["natural"="spring"]', 13, "studanka", "💧"),
    ("drinking_water", '["amenity"="drinking_water"]', 13, "pitna voda", "🚰"),
    ("monument", '["historic"~"^(monument|memorial|ruins)$"]', 15, "pamatnik", "🏛"),
    ("refreshment", '["amenity"~"^(restaurant|cafe|pub|fast_food)$"]', 16, "obcerstveni", "🍴"),
]
# Bod s odkazem na Wikipedii/Wikidata je prokazatelne znamejsi - objevi se driv.
NOTABLE_ZOOM_BONUS = 1
# Bezejmenna restaurace je sum; u vody a vyhlidek je i bezejmenny bod uzitecny.
NAME_REQUIRED = {"refreshment", "monument", "castle"}
# Pametni desticky a kameny zmizelych jsou v OSM taky `historic=memorial`, ale
# jako orientacni bod za behu nefunguji - jsou v dlazbe nebo na zdi a je jich
# rad. V okoli Karlova nam. jich je 888 z 1 277 pojmenovanych "pamatniku",
# takze bez tohoto filtru vrstvu uplne zaplavi.
SKIPPED_MEMORIALS = {"stolperstein", "plaque", "plate", "stone"}

# Zvys, kdyz se zmeni klasifikace - stara cache s jinym rozdelenim se pak
# nepouzije (jmena souboru nesou verzi).
POI_VERSION = 2


def _haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _cache_path(lat, lon, reach_km):
    return POI_DIR / f"pois_{lat:.3f}_{lon:.3f}_{reach_km:.0f}km_v{POI_VERSION}.json"


def _covering_cache_path(lat, lon, reach_km):
    best = None
    for path in POI_DIR.glob(f"pois_*km_v{POI_VERSION}.json"):
        try:
            _, clat, clon, creach, _version = path.stem.split("_")
            clat, clon, creach_km = float(clat), float(clon), float(creach.removesuffix("km"))
        except ValueError:
            continue
        if _haversine_m(lat, lon, clat, clon) / 1000 + reach_km <= creach_km + COVER_SLACK_KM:
            if best is None or creach_km < best[1]:
                best = (path, creach_km)
    return best[0] if best else None


def _min_zoom(category, base_zoom, tags):
    if tags.get("wikidata") or tags.get("wikipedia"):
        return base_zoom - NOTABLE_ZOOM_BONUS
    return base_zoom


def build_pois(lat, lon, reach_km):
    """Stahne body z Overpass. Prvni dotaz pro Prahu trva desitky sekund."""
    dist = int(max(reach_km, MIN_DOWNLOAD_REACH_KM) * 1000)
    parts = "".join(
        f"node{osm_filter}(around:{dist},{lat},{lon});"
        f"way{osm_filter}(around:{dist},{lat},{lon});"
        for _key, osm_filter, _zoom, _label, _icon in CATEGORIES
    )
    query = f"[out:json][timeout:180];({parts});out center tags;"

    points = []
    try:
        request = urllib.request.Request(
            OVERPASS_URL,
            data=urllib.parse.urlencode({"data": query}).encode(),
            headers={"User-Agent": "statshunters-route-planner"},
        )
        with urllib.request.urlopen(request, timeout=240) as response:
            elements = json.load(response).get("elements", [])
        points = _classify(elements)
    except Exception:
        points = []

    path = _cache_path(lat, lon, max(reach_km, MIN_DOWNLOAD_REACH_KM))
    path.write_text(json.dumps(points, ensure_ascii=False), encoding="utf-8")
    return points


def _matches(tags, category):
    """Zjednodusene vyhodnoceni tehoz filtru, jaky slo do Overpassu - dotaz
    vraci vsechny prvky dohromady, takze se kategorie musi priradit zpetne."""
    if category == "viewpoint":
        return tags.get("tourism") == "viewpoint"
    if category == "tower":
        return tags.get("man_made") == "tower" and tags.get("tower:type") == "observation"
    if category == "peak":
        return tags.get("natural") == "peak"
    if category == "castle":
        return tags.get("historic") in ("castle", "fort")
    if category == "spring":
        return tags.get("natural") == "spring"
    if category == "drinking_water":
        return tags.get("amenity") == "drinking_water"
    if category == "monument":
        if tags.get("memorial") in SKIPPED_MEMORIALS:
            return False
        return tags.get("historic") in ("monument", "memorial", "ruins")
    if category == "refreshment":
        return tags.get("amenity") in ("restaurant", "cafe", "pub", "fast_food")
    return False


def _classify(elements):
    """OSM prvky -> body vrstvy. Poradi CATEGORIES rozhoduje: hrad s restauraci
    zustane hradem."""
    points = []
    seen = set()
    for element in elements:
        tags = element.get("tags") or {}
        center = element.get("center") or element
        lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue

        for key, _filter, base_zoom, label, icon in CATEGORIES:
            if not _matches(tags, key):
                continue
            name = tags.get("name")
            if not name and key in NAME_REQUIRED:
                break
            identity = (key, round(lat, 5), round(lon, 5), name)
            if identity in seen:
                break
            seen.add(identity)
            points.append({
                "kind": key,
                "label": label,
                "icon": icon,
                "name": name or label,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "min_zoom": _min_zoom(key, base_zoom, tags),
            })
            break
    points.sort(key=lambda point: (point["min_zoom"], point["name"]))
    return points


_MEMORY = {}


def load_pois(lat, lon, reach_km):
    path = _covering_cache_path(lat, lon, reach_km)
    if path is None:
        data = build_pois(lat, lon, reach_km)
        _MEMORY[str(_cache_path(lat, lon, max(reach_km, MIN_DOWNLOAD_REACH_KM)))] = data
        return data
    if str(path) not in _MEMORY:
        _MEMORY[str(path)] = json.loads(path.read_text(encoding="utf-8"))
    return _MEMORY[str(path)]
