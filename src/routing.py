import math
from pathlib import Path

from geojson import lon_lat_tile, tile_lon_lat

ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = ROOT / "data"
DETOUR_FACTOR = 1.35
MAX_WAYPOINTS = 8
MAX_CANDIDATES = 60
MAX_GROUP_SEEDS = 8
MAX_SQUARE_SEEDS = 4
MAX_SQUARE_MISSING = 4
IMPROVE_ROUNDS = 2
IMPROVE_MOVES_PER_ROUND = 10


def haversine_m(lat1, lon1, lat2, lon2):
    earth_radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * earth_radius * math.asin(math.sqrt(a))


def tile_center(tile):
    x, y = tile
    lon, lat = tile_lon_lat(x + 0.5, y + 0.5)
    return lat, lon


# Preference typu cest pro beh (uzivatel 2026-07-19): cyklostezka > turisticka
# cesta/pesina > park a pesi zona > chodnik > klidna silnice; rusne silnice
# penalizovane, schody take. Nasobi delku hrany pri hledani cesty (run_cost);
# realna delka trasy se pocita zvlast ze skutecnych metru.
RUN_PREFERENCES = {
    "cycleway": 0.60,
    "path": 0.70,
    "track": 0.70,
    "bridleway": 0.75,
    "pedestrian": 0.80,
    "footway": 0.85,
    "living_street": 0.95,
    "residential": 1.0,
    "service": 1.05,
    "unclassified": 1.05,
    "road": 1.1,
    "steps": 1.4,
    "tertiary": 1.35,
    "tertiary_link": 1.35,
    "secondary": 1.7,
    "secondary_link": 1.7,
    "primary": 2.2,
    "primary_link": 2.2,
    "trunk": 3.0,
    "trunk_link": 3.0,
}
DEFAULT_RUN_FACTOR = 1.1

MIN_DOWNLOAD_REACH_KM = 10
# Reach je horni odhad (vzdusnou carou max/2 + rezerva); kandidatni tiles lezi
# nejvyse na 90 % teto vzdalenosti. Chybejici vnejsi lem grafu tedy trasu
# nerozbije, jen ji v krajnim pripade lehce zhorsi - a usetri minutove stahovani
# temer identicke oblasti z Overpass.
COVERAGE_SLACK_KM = 1.5


def graph_path(lat, lon, reach_km):
    return GRAPH_DIR / f"walk_{lat:.3f}_{lon:.3f}_{reach_km:.1f}km.graphml"


def _covering_graph_path(lat, lon, reach_km):
    """Najdi ulozeny graf, jehoz oblast pokryva pozadovany kruh (start, reach)."""
    best = None
    for path in GRAPH_DIR.glob("walk_*km.graphml"):
        try:
            _, cached_lat, cached_lon, cached_reach = path.stem.split("_")
            cached_lat, cached_lon = float(cached_lat), float(cached_lon)
            cached_reach_km = float(cached_reach.removesuffix("km"))
        except ValueError:
            continue
        distance_km = haversine_m(lat, lon, cached_lat, cached_lon) / 1000
        if distance_km + reach_km <= cached_reach_km + COVERAGE_SLACK_KM:
            if best is None or cached_reach_km < best[1]:
                best = (path, cached_reach_km)
    return best[0] if best else None


def _edge_factor(highway):
    if isinstance(highway, (list, tuple)):
        return min(
            (RUN_PREFERENCES.get(value, DEFAULT_RUN_FACTOR) for value in highway),
            default=DEFAULT_RUN_FACTOR,
        )
    return RUN_PREFERENCES.get(highway, DEFAULT_RUN_FACTOR)


def prepare_run_costs(graph):
    """Doplni hranam run_cost = delka x preference typu cesty."""
    for _u, _v, data in graph.edges(data=True):
        data["run_cost"] = float(data["length"]) * _edge_factor(data.get("highway"))
    return graph


_GRAPH_MEMORY = {}


def load_walk_graph(lat, lon, reach_km):
    """Pesi graf OSM pokryvajici kruh (start, reach). Prvni stazeni z Overpass
    trva minuty; stahuje se velkoryse (min. 10 km), aby dalsi dotazy s jinym
    startem/delkou v okoli pouzily stejny soubor z data/. Nacteny graf zustava
    v pameti procesu (samotne cteni graphml trva ~25 s)."""
    import osmnx as ox

    path = _covering_graph_path(lat, lon, reach_km)
    if path is None:
        download_reach = max(reach_km, MIN_DOWNLOAD_REACH_KM)
        ox.settings.cache_folder = str(GRAPH_DIR / "osmnx_cache")
        graph = ox.graph_from_point(
            (lat, lon), dist=download_reach * 1000, network_type="walk", simplify=True
        )
        path = graph_path(lat, lon, download_reach)
        ox.save_graphml(graph, path)
        _GRAPH_MEMORY[str(path)] = prepare_run_costs(graph)
        return _GRAPH_MEMORY[str(path)]

    if str(path) not in _GRAPH_MEMORY:
        _GRAPH_MEMORY[str(path)] = prepare_run_costs(ox.load_graphml(path))
    return _GRAPH_MEMORY[str(path)]


_NODE_INDEX_CACHE = {}


def _node_index(graph):
    import numpy as np

    key = id(graph)
    if key not in _NODE_INDEX_CACHE:
        nodes = list(graph.nodes)
        lats = np.array([graph.nodes[n]["y"] for n in nodes])
        lons = np.array([graph.nodes[n]["x"] for n in nodes])
        _NODE_INDEX_CACHE[key] = (nodes, lats, lons)
    return _NODE_INDEX_CACHE[key]


def nearest_node(node_index, lat, lon):
    import numpy as np

    nodes, lats, lons = node_index
    coslat = math.cos(math.radians(lat))
    d2 = (lats - lat) ** 2 + ((lons - lon) * coslat) ** 2
    return nodes[int(np.argmin(d2))]


def _best_edge(graph, u, v):
    return min(graph[u][v].values(), key=lambda edge: edge.get("run_cost", edge["length"]))


def _leg(graph, cache, a, b):
    """Nejlepsi usek podle run_cost (preference typu cest); vraci REALNOU delku."""
    import networkx as nx

    if (a, b) not in cache:
        try:
            _cost, path = nx.bidirectional_dijkstra(graph, a, b, weight="run_cost")
            length = _path_length_m(graph, path)
        except nx.NetworkXNoPath:
            length, path = math.inf, None
        cache[(a, b)] = (length, path)
    return cache[(a, b)]


def _trim_spurs(node_tiles, node_path):
    """Zkrati slepe ocasky (usek ke stredu tile a zpet stejnou cestou) na
    nejkratsi delku zachovavajici mnozinu protnutych tiles: spicka ocasku
    odpada, dokud jeji tile pokryva jiny uzel trasy."""
    from collections import Counter

    path = list(node_path)
    counts = Counter(node_tiles[node] for node in path)
    i = 1
    while i < len(path) - 1:
        if path[i - 1] == path[i + 1] and counts[node_tiles[path[i]]] > 1:
            counts[node_tiles[path[i]]] -= 1
            counts[node_tiles[path[i + 1]]] -= 1
            del path[i:i + 2]
            i = max(i - 1, 1)
        else:
            i += 1
    return path


def _path_length_m(graph, node_path):
    return float(sum(
        _best_edge(graph, u, v)["length"]
        for u, v in zip(node_path, node_path[1:])
    ))


def _path_coordinates(graph, node_path):
    """Souradnice trasy vcetne geometrii hran (skutecne tvary ulic, ne jen
    spojnice krizovatek) - presnejsi GPX, mapa i vypocet protnutych tiles."""
    if len(node_path) < 2:
        return [(graph.nodes[n]["y"], graph.nodes[n]["x"]) for n in node_path]

    coordinates = []
    for u, v in zip(node_path, node_path[1:]):
        edge = _best_edge(graph, u, v)
        geometry = edge.get("geometry")
        if geometry is not None:
            points = [(lat, lon) for lon, lat in geometry.coords]
            u_lat, u_lon = graph.nodes[u]["y"], graph.nodes[u]["x"]
            starts_at_u = (abs(points[0][0] - u_lat) + abs(points[0][1] - u_lon)
                           <= abs(points[-1][0] - u_lat) + abs(points[-1][1] - u_lon))
            if not starts_at_u:
                points.reverse()
        else:
            points = [
                (graph.nodes[u]["y"], graph.nodes[u]["x"]),
                (graph.nodes[v]["y"], graph.nodes[v]["x"]),
            ]
        coordinates.extend(points if not coordinates else points[1:])
    return coordinates


def plan_walk(graph, from_lat, from_lon, to_lat, to_lon):
    """Pesi/bezecky presun mezi dvema body po stejnem grafu jako behy."""
    node_index = _node_index(graph)
    node_a = nearest_node(node_index, from_lat, from_lon)
    node_b = nearest_node(node_index, to_lat, to_lon)
    length_m, path = _leg(graph, {}, node_a, node_b)
    if path is None:
        raise RuntimeError("No walkable path between the points")
    return {
        "km": round(float(length_m) / 1000, 2),
        "coordinates": _path_coordinates(graph, path),
    }


def _exact_loop(graph, cache, start_node, waypoint_nodes, end_node=None):
    order = [start_node] + waypoint_nodes + [end_node if end_node is not None else start_node]
    total = 0.0
    full_path = []
    for a, b in zip(order, order[1:]):
        length, path = _leg(graph, cache, a, b)
        if path is None:
            return math.inf, None
        total += length
        full_path.extend(path if not full_path else path[1:])
    return total, full_path


def _estimate_path_m(start, end, seq):
    points = [start] + [(item["lat"], item["lon"]) for item in seq] + [end]
    straight = sum(
        haversine_m(*points[i], *points[i + 1]) for i in range(len(points) - 1)
    )
    return DETOUR_FACTOR * straight


def _reachable(lat, lon, start, end, max_m):
    """Bod je v dosahu, kdyz se objizdka start -> bod -> end vejde do rozpoctu
    (pro okruh start == end degeneruje na kruh)."""
    detour = haversine_m(start[0], start[1], lat, lon) + haversine_m(lat, lon, end[0], end[1])
    return detour <= max_m * 0.9


def _within_reach(candidates, start, end, max_m):
    within = []
    for cand in candidates:
        lat, lon = tile_center(cand["tile"])
        if _reachable(lat, lon, start, end, max_m):
            within.append({"tile": tuple(cand["tile"]), "score": cand["score"], "lat": lat, "lon": lon})
        if len(within) >= MAX_CANDIDATES:
            break
    return within


def _candidate_groups(within):
    """Skupiny sousednich kandidatu (4-okoli) - navsteva skupiny mivat vetsi
    spolecny prinos, nez rika soucet individualnich skore."""
    by_tile = {cand["tile"]: cand for cand in within}
    remaining = set(by_tile)
    groups = []
    while remaining:
        seed = remaining.pop()
        component = [seed]
        stack = [seed]
        while stack:
            x, y = stack.pop()
            for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    component.append(neighbour)
                    stack.append(neighbour)
        groups.append([by_tile[tile] for tile in component])
    return groups


def _square_completion_seeds(within, context, start, end, max_m):
    """Seedy cilene na zvetseni max square: okna (side+1)^2 s nejvyse
    MAX_SQUARE_MISSING chybejicimi tiles, vsechny v dosahu. Jednotlive chybejici
    tiles maji samy o sobe nulovy square prinos (nesctitavost), takze by je
    obecne vyhledavani nemelo duvod kombinovat - proto dostavaji vlastni seed.
    Chybejici tile nemusi byt kandidat ze scoringu (score 0)."""
    from scoring import PRIORITY_WEIGHTS

    by_tile = {cand["tile"]: cand for cand in within}
    seeds = []
    seen = set()

    for period in ("all", "year", "recent"):
        tiles = context["period_tiles"][period]
        side = context["baselines"][period]["square_size"] + 1
        weight = PRIORITY_WEIGHTS[f"{period}_square"]

        anchors = set()
        for cand in within:
            cx, cy = cand["tile"]
            for dx in range(side):
                for dy in range(side):
                    anchors.add((cx - dx, cy - dy))

        for ax, ay in anchors:
            window = [(ax + dx, ay + dy) for dx in range(side) for dy in range(side)]
            missing = [tile for tile in window if tile not in tiles]
            if not missing or len(missing) > MAX_SQUARE_MISSING:
                continue
            key = (period, tuple(sorted(missing)))
            if key in seen:
                continue
            seen.add(key)

            waypoints = []
            for tile in missing:
                if tile in by_tile:
                    waypoints.append(by_tile[tile])
                    continue
                lat, lon = tile_center(tile)
                if not _reachable(lat, lon, start, end, max_m):
                    waypoints = None
                    break
                waypoints.append({"tile": tile, "score": 0.0, "lat": lat, "lon": lon})
            if waypoints:
                seeds.append((weight * (2 * side - 1), len(missing), waypoints))

    seeds.sort(key=lambda item: (-item[0], item[1]))
    return [waypoints for _, _, waypoints in seeds[:MAX_SQUARE_SEEDS]]


def _greedy_fill(start, end, sequence, pool, max_m):
    """Cheapest insertion na odhadech: doplnuje kandidaty z poolu, dokud se
    odhad delky vejde pod max_m."""
    sequence = list(sequence)
    used = {item["tile"] for item in sequence}
    for cand in pool:
        if len(sequence) >= MAX_WAYPOINTS:
            break
        if cand["tile"] in used:
            continue
        best = None
        for pos in range(len(sequence) + 1):
            trial = sequence[:pos] + [cand] + sequence[pos:]
            estimate = _estimate_path_m(start, end, trial)
            if estimate <= max_m and (best is None or estimate < best[0]):
                best = (estimate, trial)
        if best:
            sequence = best[1]
            used.add(cand["tile"])
    return sequence


def _route_details(graph, leg_cache, node_index, start_node, sequence, min_m, max_m, context, end_node=None):
    """Exaktni trasa pro sekvenci waypointu + spolecny prinos protnutych tiles.
    Pri prekroceni max_m odpada nejslabsi waypoint."""
    from scoring import evaluate_tile_set

    sequence = list(sequence)
    while True:
        waypoint_nodes = [nearest_node(node_index, item["lat"], item["lon"]) for item in sequence]
        length_m, node_path = _exact_loop(graph, leg_cache, start_node, waypoint_nodes, end_node)
        if node_path is None:
            return None
        node_tiles = {
            node: lon_lat_tile(graph.nodes[node]["x"], graph.nodes[node]["y"])
            for node in node_path
        }
        node_path = _trim_spurs(node_tiles, node_path)
        length_m = _path_length_m(graph, node_path)
        if length_m <= max_m or not sequence:
            break
        weakest = min(sequence, key=lambda item: item["score"])
        sequence = [item for item in sequence if item is not weakest]

    coordinates = _path_coordinates(graph, node_path)
    crossed = {lon_lat_tile(lon, lat) for lat, lon in coordinates}
    return {
        "sequence": sequence,
        "length_m": length_m,
        "coordinates": coordinates,
        "tiles_crossed": sorted(crossed),
        "benefit": evaluate_tile_set(crossed, context),
        "in_window": min_m <= length_m <= max_m,
    }


def _variant_key(details):
    return (details["in_window"], details["benefit"]["total"], -details["length_m"])


def plan_tile_loop(graph, start_lat, start_lon, target_km, tolerance_km, candidates, context,
                   end_lat=None, end_lon=None):
    """Beh v delce target +- tolerance s nejvetsim spolecnym prinosem.

    Okruh (end == start, vychozi), nebo z bodu do bodu (end_lat/end_lon).
    Porovnava varianty: rank-greedy seed + seedy kolem skupin sousednich kandidatu
    + seedy na dokompletovani square, kazdou exaktne prepocita a ohodnoti
    spolecnym prinosem VSECH protnutych tiles (evaluate_tile_set - zisky mnoziny,
    ne soucet skore). Vitez se jeste zkousi vylepsit pridavanim kandidatu.
    """
    min_m = (target_km - tolerance_km) * 1000
    max_m = (target_km + tolerance_km) * 1000
    start = (start_lat, start_lon)
    end = (end_lat, end_lon) if end_lat is not None else start
    is_loop = end == start

    within = _within_reach(candidates, start, end, max_m)
    node_index = _node_index(graph)
    start_node = nearest_node(node_index, start_lat, start_lon)
    end_node = None if is_loop else nearest_node(node_index, end[0], end[1])
    leg_cache = {}

    def details_for(sequence):
        return _route_details(
            graph, leg_cache, node_index, start_node, sequence, min_m, max_m, context, end_node
        )

    variants = []
    base = details_for(_greedy_fill(start, end, [], within, max_m))
    if base:
        variants.append(base)

    # Vyber skupin podle souctu skore je jen levny proxy pro poradi seedu;
    # rozhoduje az spolecny prinos exaktnich variant.
    groups = sorted(
        _candidate_groups(within),
        key=lambda group: sum(member["score"] for member in group),
        reverse=True,
    )[:MAX_GROUP_SEEDS]
    for group in groups:
        seed = _greedy_fill(start, end, [], sorted(group, key=lambda m: -m["score"]), max_m)
        if not seed:
            continue
        filled = _greedy_fill(start, end, seed, within, max_m)
        details = details_for(filled)
        if details:
            variants.append(details)

    for square_seed in _square_completion_seeds(within, context, start, end, max_m):
        seed = _greedy_fill(start, end, [], sorted(square_seed, key=lambda m: -m["score"]), max_m)
        if len(seed) < len(square_seed):
            continue
        filled = _greedy_fill(start, end, seed, within, max_m)
        details = details_for(filled)
        if details:
            variants.append(details)

    if not variants:
        raise RuntimeError("No walkable route found from the start point")

    best = max(variants, key=_variant_key)

    for _ in range(IMPROVE_ROUNDS):
        used = {item["tile"] for item in best["sequence"]}
        pool = [cand for cand in within if cand["tile"] not in used][:IMPROVE_MOVES_PER_ROUND]
        improved = False
        for cand in pool:
            if len(best["sequence"]) >= MAX_WAYPOINTS:
                break
            trial = _greedy_fill(start, end, best["sequence"], [cand], max_m)
            if len(trial) == len(best["sequence"]):
                continue
            details = details_for(trial)
            if details and _variant_key(details) > _variant_key(best):
                best = details
                improved = True
        if not improved:
            break

    return {
        "length_km": round(best["length_m"] / 1000, 2),
        "target_km": target_km,
        "tolerance_km": tolerance_km,
        "within_target": best["in_window"],
        "start": {"lat": start_lat, "lon": start_lon},
        "end": {"lat": end[0], "lon": end[1]},
        "is_loop": is_loop,
        "waypoint_tiles": [item["tile"] for item in best["sequence"]],
        "tiles_crossed": best["tiles_crossed"],
        "coordinates": best["coordinates"],
        "benefit": best["benefit"],
        "variants_compared": len(variants),
    }


def route_to_gpx(coordinates, name="StatsHunters route"):
    points = "\n".join(
        f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}"></trkpt>'
        for lat, lon in coordinates
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="statshunters-route-planner" '
        'xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"  <trk>\n    <name>{name}</name>\n    <trkseg>\n{points}\n"
        "    </trkseg>\n  </trk>\n</gpx>\n"
    )


def main():
    import argparse

    import yaml

    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description="Plan a running loop through top-scored tiles")
    parser.add_argument("--lat", type=float, default=config["home"]["lat"], help="start latitude")
    parser.add_argument("--lon", type=float, default=config["home"]["lon"], help="start longitude")
    parser.add_argument("--distance", type=float, default=config["target_distance_km"], help="target km")
    parser.add_argument("--tolerance", type=float, default=config["distance_tolerance_km"], help="tolerance km")
    parser.add_argument("--gpx", default=None, help="write GPX to this path")
    args = parser.parse_args()

    from api import get_period_tile_database
    from scoring import build_route_context, find_tile_opportunities

    tile_dbs = {key: get_period_tile_database(key) for key in ("all", "year", "recent")}
    opportunities = find_tile_opportunities(tile_dbs)
    context = build_route_context(tile_dbs)

    reach_km = (args.distance + args.tolerance) / 2 + 0.5
    print(f"Loading walk graph around {args.lat:.4f}, {args.lon:.4f} (reach {reach_km:.1f} km)...")
    graph = load_walk_graph(args.lat, args.lon, reach_km)
    print(f"Graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges")

    route = plan_tile_loop(graph, args.lat, args.lon, args.distance, args.tolerance, opportunities, context)

    candidate_tiles = {tuple(o["tile"]) for o in opportunities}
    crossed_candidates = [t for t in route["tiles_crossed"] if t in candidate_tiles]
    print(f"\nLoop length: {route['length_km']} km (target {args.distance}+-{args.tolerance})")
    print(f"Waypoint tiles: {route['waypoint_tiles']}")
    print(f"Tiles crossed: {len(route['tiles_crossed'])}, of that recommended: {len(crossed_candidates)}")
    print(f"Variants compared: {route['variants_compared']}")
    print(f"Benefit total: {route['benefit']['total']} (staleness {route['benefit']['staleness']})")
    for key, gain in route["benefit"]["gains"].items():
        if gain:
            print(f"  {key}: +{gain}")

    if args.gpx:
        Path(args.gpx).write_text(route_to_gpx(route["coordinates"]), encoding="utf-8")
        print(f"GPX written to {args.gpx}")


if __name__ == "__main__":
    main()
