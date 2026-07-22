"""Orientacni body, ktere pesi graf neobsahuje: vodni toky a zeleznice.

osmnx u network_type='walk' zeleznice ani vodu nestahuje, takze se stahuji zvlast
jako geometrie (features) a krizeni s trasou se hleda geometricky. Vysledek se
cachuje stejne velkoryse jako pesi graf (pokryti + rezerva).
"""
import json
import math
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

EARTH_RADIUS_M = 6371000.0


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


def build_barriers(lat, lon, reach_km):
    import osmnx as ox

    ox.settings.cache_folder = str(BARRIER_DIR / "osmnx_cache")
    dist = max(reach_km, MIN_DOWNLOAD_REACH_KM) * 1000
    lines = []

    try:
        water = ox.features_from_point(
            (lat, lon), tags={"waterway": ["river", "stream", "canal"]}, dist=dist
        )
        lines += _extract_lines(water, lambda row: row.get("name") or "vodni tok")
    except Exception:
        pass

    try:
        rail = ox.features_from_point((lat, lon), tags={"railway": ["rail"]}, dist=dist)
        # nazvy trati jsou nepouzitelne ("kolej 102", "Tunel II") - genericky popis
        lines += _extract_lines(rail, lambda row: "žel. trať")
    except Exception:
        pass

    path = _cache_path("barriers", lat, lon, max(reach_km, MIN_DOWNLOAD_REACH_KM))
    path.write_text(json.dumps(lines), encoding="utf-8")
    return lines


def build_streets(lat, lon, reach_km):
    """Pojmenovane ulice jako [[name, is_major, [[lon,lat],...]], ...]."""
    import osmnx as ox

    ox.settings.cache_folder = str(BARRIER_DIR / "osmnx_cache")
    dist = max(reach_km, MIN_DOWNLOAD_REACH_KM) * 1000
    lines = []
    try:
        features = ox.features_from_point((lat, lon), tags={"highway": STREET_CLASSES}, dist=dist)
        named = features[features["name"].notna()] if "name" in features.columns else features.iloc[:0]
        for geometry, name, highway in zip(named.geometry, named["name"], named.get("highway", "")):
            major = str(highway) in MAJOR_STREET_CLASSES
            label = name[0] if isinstance(name, list) else name
            for _, coords in _iter_lines(geometry, label):
                lines.append([label, major, coords])
    except Exception:
        pass

    path = _cache_path("streets", lat, lon, max(reach_km, MIN_DOWNLOAD_REACH_KM))
    path.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    return lines


_CACHE_MEMORY = {}


def _load_cached(prefix, builder, lat, lon, reach_km):
    path = _covering_cache_path(prefix, lat, lon, reach_km)
    if path is None:
        data = builder(lat, lon, reach_km)
        _CACHE_MEMORY[str(_cache_path(prefix, lat, lon, max(reach_km, MIN_DOWNLOAD_REACH_KM)))] = data
        return data
    if str(path) not in _CACHE_MEMORY:
        _CACHE_MEMORY[str(path)] = json.loads(path.read_text(encoding="utf-8"))
    return _CACHE_MEMORY[str(path)]


def load_barriers(lat, lon, reach_km):
    return _load_cached("barriers", build_barriers, lat, lon, reach_km)


def load_streets(lat, lon, reach_km):
    return _load_cached("streets", build_streets, lat, lon, reach_km)


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

    geoms = [LineString(coords) for _label, coords in barriers if len(coords) >= 2]
    labels = [label for label, coords in barriers if len(coords) >= 2]
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
