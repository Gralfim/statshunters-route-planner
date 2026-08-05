"""Orientacni body, ktere pesi graf neobsahuje: vodni toky a zeleznice.

osmnx u network_type='walk' zeleznice ani vodu nestahuje, takze se stahuji zvlast
jako geometrie (features) a krizeni s trasou se hleda geometricky. Vysledek se
cachuje stejne velkoryse jako pesi graf (pokryti + rezerva).
"""
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BARRIER_DIR = ROOT / "data"
COVER_SLACK_KM = 1.5
MIN_DOWNLOAD_REACH_KM = 10
CROSS_DEDUP_M = 400.0   # stejny nazev do teto vzdalenosti = tentyz prechod

# Pesi graf (network_type='walk') vede chodniky jako nepojmenovane cesty a osy
# ulic z velke casti vynechava (Ke Karlovu 0 hran, Jecna 2). Pro pojmenovani
# chodniku a krizeni se proto pojmenovane ulice stahuji zvlast.
STREET_CLASSES = [
    "primary", "secondary", "tertiary", "residential",
    "living_street", "unclassified", "pedestrian",
]
MAJOR_STREET_CLASSES = {"primary", "secondary", "tertiary"}

# Znacene trasy (KCT, cyklotrasy) jsou v OSM relace - osmnx features je nevraci,
# proto primy dotaz na Overpass.
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


class SourceUnavailable(Exception):
    """Zdroj se nepodarilo stahnout. Ma vlastni typ, aby se nedal splest
    s prazdnym vysledkem - prazdny vysledek se cachuje, selhani ne."""

TRAIL_COLORS = {
    "red": "cervena", "blue": "modra", "green": "zelena", "yellow": "zluta",
    "white": "bila", "black": "cerna", "orange": "oranzova", "purple": "fialova",
}

# Cyklotrasy nesou v OSM stav a prazska sit je vedena DVOJMO: vedle relace
# skutecne trasy existuje relace navrhu. `state=proposed` je planovana trasa,
# `state=recommended` doporucena - tedy doporuceni cyklokoordinatora, v terenu
# NEZNACENE. Bez filtru posila itinerar bezce po znaceni, ktere neexistuje.
# Zmereno na 405 cyklorelacich v okoli Prahy: 38 proposed, 232 recommended,
# 135 skutecnych. Vsech 100 tras s prefixem X ("klidova alternativa", X13 se
# jmenuje doslova "Klidova alternativa bud. A13") je recommended, stejne jako
# A135 nebo A235. U 53 cisel filtr necha skutecnou verzi a zahodi jen dvojce
# "navrh" (A1, A13, A120).
# Turistickych tras se to netyka - `state` nepouzivaji vubec (113 ze 113
# relaci), a taky jsou v CR znacene spolehlive.
UNSIGNED_ROUTE_STATES = {"proposed", "recommended"}
# Verze v nazvu cache: stazena data nesou uz jen popisky, ne stav relace,
# takze filtr na starou cache nedosahne - musi se stahnout znovu. Pomlcka,
# ne podtrzitko: nazev souboru se parsuje pres split("_").
TRAILS_CACHE = "trails-v2"

EARTH_RADIUS_M = 6371000.0


def _text(value, default=None):
    """Ocisti hodnotu z pandas: chybejici je float nan (a nan je truthy, takze
    `value or default` NEfunguje) - vrat default. Prvni z listu vezme."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    text = str(value).strip()
    return text if text and text.lower() != "nan" else default


def _haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _cache_path(prefix, lat, lon, reach_km):
    return BARRIER_DIR / f"{prefix}_{lat:.3f}_{lon:.3f}_{reach_km:.0f}km.json"


def _covering_cache_path(prefix, lat, lon, reach_km):
    best = None
    for path in BARRIER_DIR.glob(f"{prefix}_*km.json"):
        try:
            _, clat, clon, creach = path.stem.split("_")
            clat, clon, creach_km = float(clat), float(clon), float(creach.removesuffix("km"))
        except ValueError:
            continue
        if _haversine_m(lat, lon, clat, clon) / 1000 + reach_km <= creach_km + COVER_SLACK_KM:
            if best is None or creach_km < best[1]:
                best = (path, creach_km)
    return best[0] if best else None


def _part_coords(part):
    """Souradnice linie/hranice geometrie. Polygon.coords vyhazuje vyjimku
    (nema jednu sekvenci), proto se bere exterior."""
    if hasattr(part, "exterior"):
        part = part.exterior
    try:
        return list(part.coords)
    except (NotImplementedError, AttributeError):
        return None


def _iter_lines(geometry, label):
    if geometry is None or geometry.is_empty:
        return
    for part in getattr(geometry, "geoms", [geometry]):
        coords = _part_coords(part)
        if coords and len(coords) >= 2:
            yield [label, [[round(x, 6), round(y, 6)] for x, y in coords]]


def _extract_lines(features, label_fn):
    """LineStringy (i z hranic polygonu) -> [[label, [[lon,lat],...]]]."""
    lines = []
    for geometry, label in zip(features.geometry, features.apply(label_fn, axis=1)):
        lines.extend(_iter_lines(geometry, label))
    return lines


def _features(ox, lat, lon, dist, tags):
    """Geometrie z osmnx, nebo None kdyz v okoli zadne nejsou.

    Rozlisuje "nic tam neni" od "nepodarilo se stahnout": prvni je platny
    vysledek (osmnx hlasi InsufficientResponseError), druhe vyjimka, aby se
    neuplny vysledek nezapsal do cache jako hotovy.
    """
    from osmnx._errors import InsufficientResponseError

    try:
        return ox.features_from_point((lat, lon), tags=tags, dist=dist)
    except InsufficientResponseError:
        return None
    except Exception as error:
        raise SourceUnavailable(f"osmnx {sorted(tags)}: {error}") from error


def build_barriers(lat, lon, reach_km):
    import osmnx as ox

    ox.settings.cache_folder = str(BARRIER_DIR / "osmnx_cache")
    dist = max(reach_km, MIN_DOWNLOAD_REACH_KM) * 1000
    lines = []
    water = _features(ox, lat, lon, dist, {"waterway": ["river", "stream", "canal"]})
    if water is not None:
        lines += _extract_lines(water, lambda row: _text(row.get("name"), "vodni tok"))
    rail = _features(ox, lat, lon, dist, {"railway": ["rail"]})
    if rail is not None:
        # nazvy trati jsou nepouzitelne ("kolej 102", "Tunel II") - genericky popis
        lines += _extract_lines(rail, lambda row: "žel. trať")

    path = _cache_path("barriers", lat, lon, max(reach_km, MIN_DOWNLOAD_REACH_KM))
    path.write_text(json.dumps(lines), encoding="utf-8")
    return lines


def build_streets(lat, lon, reach_km):
    """Pojmenovane ulice jako [[name, is_major, [[lon,lat],...]], ...]."""
    import osmnx as ox

    ox.settings.cache_folder = str(BARRIER_DIR / "osmnx_cache")
    dist = max(reach_km, MIN_DOWNLOAD_REACH_KM) * 1000
    lines = []
    features = _features(ox, lat, lon, dist, {"highway": STREET_CLASSES})
    if features is not None:
        named = features[features["name"].notna()] if "name" in features.columns else features.iloc[:0]
        for geometry, name, highway in zip(named.geometry, named["name"], named.get("highway", "")):
            label = _text(name)
            if label is None:
                continue
            major = str(highway) in MAJOR_STREET_CLASSES
            for _, coords in _iter_lines(geometry, label):
                lines.append([label, major, coords])

    path = _cache_path("streets", lat, lon, max(reach_km, MIN_DOWNLOAD_REACH_KM))
    path.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    return lines


def _trail_label(tags):
    """Popis znacene trasy: turisticka podle barvy znacky, cyklotrasa podle cisla."""
    route = tags.get("route")
    if route in ("hiking", "foot"):
        symbol = _text(tags.get("osmc:symbol"), "")
        colour = symbol.split(":")[0] if symbol else _text(tags.get("colour"), "")
        czech = TRAIL_COLORS.get(colour.lower())
        return f"{czech} turisticka" if czech else "turisticka znacka"
    if route == "bicycle":
        if _text(tags.get("state"), "") in UNSIGNED_ROUTE_STATES:
            return None
        if _text(tags.get("complete"), "") == "proposed":
            return None
        ref = _text(tags.get("ref"))
        return f"cyklotrasa {ref}" if ref else "cyklotrasa"
    return None


def _overpass(query):
    """Odpoved Overpassu, nebo vyjimka. Zkousi se vic zrcadel: hlavni server
    pod zatezi vraci 504 a jedno selhani nesmi pripravit trasu o znacky."""
    last = None
    for url in OVERPASS_MIRRORS:
        try:
            request = urllib.request.Request(
                url,
                data=urllib.parse.urlencode({"data": query}).encode(),
                headers={"User-Agent": "statshunters-route-planner"},
            )
            with urllib.request.urlopen(request, timeout=240) as response:
                return json.load(response).get("elements", [])
        except Exception as error:      # 504, timeout, docasny vypadek zrcadla
            last = error
    raise SourceUnavailable(f"Overpass neodpovedel ({last})") from last


def build_trails(lat, lon, reach_km):
    """Znacene turisticke a cyklo trasy jako [[label, [[lon,lat],...]], ...].

    Pri selhani stahovani vyhazuje SourceUnavailable - vysledek se NESMI ulozit
    do cache. Drive se chyba spolkla a do cache se zapsal prazdny seznam, takze
    jedno 504 od Overpassu pripravilo celou oblast o znacene trasy natrvalo
    (a protoze znacka zlevnuje hrany, zmenilo to i navrzenou trasu).
    """
    dist = int(max(reach_km, MIN_DOWNLOAD_REACH_KM) * 1000)
    # Ctverec, ne `around:`. Vyhledani relaci podle vzdalenosti je pro Overpass
    # rad drazsi nez bbox - na 12 km kolem Prahy vracela vsechna zrcadla 504,
    # tyz dotaz pres bbox dobehne za 7 s. Ctverec je nadmnozina kruhu a znacky
    # se stejne prirazuji geometricky, takze prebytek nevadi.
    dlat = dist / 111320.0
    dlon = dist / (111320.0 * math.cos(math.radians(lat)))
    bbox = f"{lat - dlat:.5f},{lon - dlon:.5f},{lat + dlat:.5f},{lon + dlon:.5f}"
    query = (
        f"[out:json][timeout:180][bbox:{bbox}];"
        "(relation[route~'^(hiking|foot|bicycle)$'];);"
        "out geom;"
    )
    lines = []
    for element in _overpass(query):
        label = _trail_label(element.get("tags", {}))
        if not label:
            continue
        for member in element.get("members", []):
            geometry = member.get("geometry")
            if member.get("type") != "way" or not geometry or len(geometry) < 2:
                continue
            lines.append([label, [[round(p["lon"], 6), round(p["lat"], 6)] for p in geometry]])

    path = _cache_path(TRAILS_CACHE, lat, lon, max(reach_km, MIN_DOWNLOAD_REACH_KM))
    path.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    return lines


_CACHE_MEMORY = {}


def _load_cached(prefix, builder, lat, lon, reach_km):
    path = _covering_cache_path(prefix, lat, lon, reach_km)
    if path is None:
        try:
            data = builder(lat, lon, reach_km)
        except SourceUnavailable as error:
            # Planovani pokracuje bez tohoto zdroje, ale nezapamatuje si to -
            # ani na disk, ani do pameti. Priste se zkusi znovu.
            print(f"VAROVANI: {prefix} se nepodarilo stahnout: {error}", file=sys.stderr)
            return []
        _CACHE_MEMORY[str(_cache_path(prefix, lat, lon, max(reach_km, MIN_DOWNLOAD_REACH_KM)))] = data
        return data
    if str(path) not in _CACHE_MEMORY:
        _CACHE_MEMORY[str(path)] = json.loads(path.read_text(encoding="utf-8"))
    return _CACHE_MEMORY[str(path)]


def load_barriers(lat, lon, reach_km):
    return _load_cached("barriers", build_barriers, lat, lon, reach_km)


def load_streets(lat, lon, reach_km):
    return _load_cached("streets", build_streets, lat, lon, reach_km)


def load_trails(lat, lon, reach_km):
    return _load_cached(TRAILS_CACHE, build_trails, lat, lon, reach_km)


def line_segments(lines):
    """Rozseka [[label, coords], ...] na segmenty: (lats, lons, bearings, labels)."""
    return street_segments([[label, False, coords] for label, coords in lines])[:4]


def street_segments(streets):
    """Rozseka pojmenovane ulice na segmenty pro prostorovy index:
    (lats, lons, bearings_0_180, names, major) jako numpy pole."""
    import numpy as np

    lats, lons, bearings, names, major = [], [], [], [], []
    for name, is_major, coords in streets:
        for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
            lats.append((y1 + y2) / 2)
            lons.append((x1 + x2) / 2)
            dlon = math.radians(x2 - x1)
            yb = math.sin(dlon) * math.cos(math.radians(y2))
            xb = (math.cos(math.radians(y1)) * math.sin(math.radians(y2))
                  - math.sin(math.radians(y1)) * math.cos(math.radians(y2)) * math.cos(dlon))
            bearings.append((math.degrees(math.atan2(yb, xb)) % 180))
            names.append(name)
            major.append(is_major)
    return (np.array(lats), np.array(lons), np.array(bearings),
            np.array(names, dtype=object), np.array(major))


def _segment_hits(barriers, tree, geoms, labels, p1, p2):
    from shapely.geometry import LineString

    segment = LineString([(p1[1], p1[0]), (p2[1], p2[0])])
    hits = set()
    for index in tree.query(segment):
        if segment.intersects(geoms[index]):
            hits.add(labels[index])
    return hits


def barrier_crossings(coordinates, barriers):
    """Krizeni trasy s vodou/zeleznici jako [(cumulative_m, label)], serazene."""
    if not barriers or len(coordinates) < 2:
        return []

    from shapely.geometry import LineString
    from shapely.strtree import STRtree

    # _text ochrani i starou cache, kde nepojmenovany tok mohl ulozit nan
    geoms = [LineString(coords) for _label, coords in barriers if len(coords) >= 2]
    labels = [_text(label, "vodni tok") for label, coords in barriers if len(coords) >= 2]
    if not geoms:
        return []
    tree = STRtree(geoms)

    crossings = []
    cumulative = 0.0
    last_at = {}
    for p1, p2 in zip(coordinates, coordinates[1:]):
        for label in _segment_hits(barriers, tree, geoms, labels, p1, p2):
            if cumulative - last_at.get(label, -math.inf) >= CROSS_DEDUP_M:
                crossings.append((cumulative, label))
            last_at[label] = cumulative
        cumulative += _haversine_m(p1[0], p1[1], p2[0], p2[1])
    return crossings


def annotate_directions(directions, coordinates, lat, lon, reach_km):
    """Doplni krokum itinerare krizeni vody/zeleznice (mimo krizovane ulice,
    ktere uz zna route_directions z grafu)."""
    barriers = load_barriers(lat, lon, reach_km)
    crossings = barrier_crossings(coordinates, barriers)
    if not crossings:
        return directions

    index = 0
    for step in directions:
        start_m = step["at_km"] * 1000
        end_m = start_m + step["km"] * 1000
        existing = {c["name"] for c in step["crossings"]}
        while index < len(crossings) and crossings[index][0] < start_m:
            index += 1
        while index < len(crossings) and crossings[index][0] <= end_m:
            distance_m, label = crossings[index]
            if label not in existing:
                existing.add(label)
                step["crossings"].append({"name": label, "at_km": round(distance_m / 1000, 2)})
            index += 1

    for step in directions:
        step["crossings"].sort(key=lambda crossing: crossing["at_km"])
    return directions
