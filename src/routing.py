import math
from collections import defaultdict
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
# Penalizace opakovaneho pruchodu stejnou ulici. Pri hledani useku se uz pouzita
# hrana zdrazi (REUSE_PENALTY x run_cost), takze se dalsi usek vraci jinudy.
# Opakovani je MEKKA slozka cilove funkce: skore = prinos - REPEAT_PENALTY_PER_KM
# x opakovane_km. Prinos zustava dominantni (vaha na urovni ~1 tile za km), takze
# opakovani rozhoduje mezi jinak srovnatelnymi trasami a nikdy neobetuje vyrazny
# prinos - v souladu se zasadou "prinos king".
REUSE_PENALTY = 4.0
# Penalizace je PODILOVA (opakovane metry / delka trasy), aby netlacila na
# zkracovani trasy - absolutni km/trasu delaly z nejkratsiho okruhu vzdy vitezze.
# Skore = prinos x (1 - REPEAT_PENALTY_FRACTION x podil opakovani).
REPEAT_PENALTY_FRACTION = 0.5
# Kolik nejlepsich variant se navic prepocita s vyhybanim opakovanym ulicim
# (nizkoopakovaci varianty maji byt v portfoliu, ne az finalizaci vitezze).
AVOID_VARIANTS = 3
AVOID_MIN_RATIO = 0.03  # pod timto podilem opakovani se avoid varianta nepocita
# Kolik tiles navic smi trasa pobrat, aby se dostala na spodni hranici tolerance
# (waypointy mirici na okraj tile zkracuji trasu - delku je pak treba dotahnout).
MAX_FILL_ROUNDS = 5
# Waypoint se umistuje dovnitr tile, ne do jeho stredu - ale s rezervou od
# hranice, aby tile zustal navstiveny i pri chybe GPS nebo navigace pri behu.
TILE_MARGIN_M = 75.0
# Efektivni "polomer" tile pro odhady delky: trasa miri na okraj bezpecne zony,
# ne do stredu (tile ma v nasich sirkach ~1570 m, pulka 785 m minus rezerva).
# Bez teto korekce odhady nadhodnocovaly delku 1,5-4x a greedy prestal pridavat
# tiles drive, nez trasa dosahla spodni hranice tolerance.
TILE_EFFECTIVE_RADIUS_M = 700.0


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


# Typy cest, kterym se dohleda ulice, podel ktere vedou (chodniky/stezky bez
# vlastniho jmena). Pojmenovani se resi jednou pri priprave grafu (atribut
# along_street), ne pri kazdem itinerari.
NAMEABLE_HIGHWAYS = {"footway", "path", "track", "pedestrian", "steps", "cycleway", "bridleway"}
FOOTLIKE_HIGHWAYS = {"footway", "steps", "pedestrian"}


def prepare_run_costs(graph):
    """Doplni hranam run_cost = delka x preference typu cesty."""
    for _u, _v, data in graph.edges(data=True):
        data["run_cost"] = float(data["length"]) * _edge_factor(data.get("highway"))
    return graph


def enrich_streets(graph, lat, lon, reach_km):
    """Chodnikum/stezkam bez jmena priradi ulici, podel ktere vedou (atribut
    along_street), a ulozi index pojmenovanych ulic na graf (graph.graph). Osy
    ulic v pesim grafu casto chybi (Ke Karlovu 0 hran), proto se beru z externiho
    zdroje pojmenovanych ulic. Bezi jednou per graf (graf se drzi v pameti)."""
    import numpy as np
    from scipy.spatial import cKDTree

    import landmarks

    segments = landmarks.street_segments(landmarks.load_streets(lat, lon, reach_km))
    graph.graph["street_segments"] = segments
    slats, slons, sbear, snames, _major = segments
    if not len(snames):
        return graph

    lat0 = float(slats.mean())
    coslat = math.cos(math.radians(lat0))
    tree = cKDTree(np.column_stack((slons * 111320.0 * coslat, slats * 111320.0)))

    targets, mids, owns = [], [], []
    for u, v, data in graph.edges(data=True):
        if data.get("name") or _tag(data, "highway") not in NAMEABLE_HIGHWAYS:
            continue
        mlat = (graph.nodes[u]["y"] + graph.nodes[v]["y"]) / 2
        mlon = (graph.nodes[u]["x"] + graph.nodes[v]["x"]) / 2
        targets.append(data)
        mids.append((mlon * 111320.0 * coslat, mlat * 111320.0))
        owns.append(_bearing(graph, u, v) % 180)
    if not targets:
        return graph

    k = min(6, len(snames))
    dists, idxs = tree.query(np.array(mids), k=k)
    dists = np.atleast_2d(dists.T).T
    idxs = np.atleast_2d(idxs.T).T
    owns = np.array(owns)
    for i, data in enumerate(targets):
        for j in range(k):
            if dists[i, j] > STREET_MATCH_MAX_M:
                break
            diff = abs(sbear[idxs[i, j]] - owns[i])
            diff = min(diff, 180 - diff)
            if diff <= STREET_MATCH_MAX_DEG:
                data["along_street"] = str(snames[idxs[i, j]])
                break
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
        _GRAPH_MEMORY[str(path)] = _prepare(graph, lat, lon, download_reach)
        return _GRAPH_MEMORY[str(path)]

    if str(path) not in _GRAPH_MEMORY:
        _GRAPH_MEMORY[str(path)] = _prepare(ox.load_graphml(path), lat, lon, reach_km)
    return _GRAPH_MEMORY[str(path)]


def _prepare(graph, lat, lon, reach_km):
    prepare_run_costs(graph)
    try:
        enrich_streets(graph, lat, lon, reach_km)
    except Exception:
        graph.graph.setdefault("street_segments", None)
    return graph


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


_TILE_NODES_CACHE = {}


def _tile_interior_nodes(graph, node_index, tile):
    """Uzly lezici uvnitr tile s rezervou TILE_MARGIN_M od hranice.

    Rezerva je pojistka proti chybe GPS/navigace: trasa jen tecna k hranici by
    tile pri par metrech odchylky nemusela zapocitat. Kdyz v bezpecne zone zadna
    cesta neni, ustupuje se na cely tile a nakonec na stred (fallbacky)."""
    import numpy as np

    key = (id(graph), tile)
    if key not in _TILE_NODES_CACHE:
        nodes, lats, lons = node_index
        x, y = tile
        west, north = tile_lon_lat(x, y)
        east, south = tile_lon_lat(x + 1, y + 1)
        dlat = TILE_MARGIN_M / 111320.0
        dlon = TILE_MARGIN_M / (111320.0 * math.cos(math.radians((north + south) / 2)))

        inside = (lats >= south) & (lats <= north) & (lons >= west) & (lons <= east)
        safe = inside & (
            (lats >= south + dlat) & (lats <= north - dlat)
            & (lons >= west + dlon) & (lons <= east - dlon)
        )
        index = np.flatnonzero(safe)
        if not len(index):
            index = np.flatnonzero(inside)
        _TILE_NODES_CACHE[key] = ([nodes[i] for i in index], lats[index], lons[index])
    return _TILE_NODES_CACHE[key]


def _pick_waypoint_node(graph, node_index, tile, previous, following):
    """Uzel v tile, ktery nejmene zajizdi: minimalizuje vzdalenost od
    predchoziho bodu trasy k nemu a dal k nasledujicimu cili. Trasa se tak tile
    dotkne tam, kudy stejne vede, misto zajizdky do geometrickeho stredu."""
    import numpy as np

    nodes, lats, lons = _tile_interior_nodes(graph, node_index, tile)
    if not len(nodes):
        return nearest_node(node_index, *tile_center(tile))

    coslat = math.cos(math.radians(previous[0]))
    to_previous = np.hypot(lats - previous[0], (lons - previous[1]) * coslat)
    to_following = np.hypot(lats - following[0], (lons - following[1]) * coslat)
    return nodes[int(np.argmin(to_previous + to_following))]


def _best_edge(graph, u, v):
    return min(graph[u][v].values(), key=lambda edge: edge.get("run_cost", edge["length"]))


def _edge_id(u, v):
    """Neorientovana identita ulice (usek mezi dvema krizovatkami)."""
    return (u, v) if u <= v else (v, u)


def _reuse_weight(used_edges):
    """Vaha pro dijkstru penalizujici hrany uz pouzite na trase."""
    def weight(u, v, edges):
        best = min(edges.values(), key=lambda edge: edge.get("run_cost", edge["length"]))
        cost = best.get("run_cost", best["length"])
        return cost * REUSE_PENALTY if _edge_id(u, v) in used_edges else cost
    return weight


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


def _leg_avoiding(graph, a, b, used_edges):
    """Usek a->b, ktery se vyhyba uz pouzitym hranam (zdrazenym REUSE_PENALTY)."""
    import networkx as nx

    try:
        _cost, path = nx.bidirectional_dijkstra(graph, a, b, weight=_reuse_weight(used_edges))
    except nx.NetworkXNoPath:
        return math.inf, None
    return _path_length_m(graph, path), path


def _repeated_m(graph, node_path):
    """Metry hran pouzitych na trase vicekrat (druhy+ pruchod stejnou ulici)."""
    from collections import Counter

    counts = Counter(_edge_id(u, v) for u, v in zip(node_path, node_path[1:]))
    return float(sum(
        _best_edge(graph, u, v)["length"] * (count - 1)
        for (u, v), count in counts.items() if count > 1
    ))


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


def _exact_loop(graph, cache, start_node, waypoint_nodes, end_node=None, avoid_reuse=False):
    order = [start_node] + waypoint_nodes + [end_node if end_node is not None else start_node]
    total = 0.0
    full_path = []
    used_edges = set()
    for a, b in zip(order, order[1:]):
        if avoid_reuse:
            length, path = _leg_avoiding(graph, a, b, used_edges)
        else:
            length, path = _leg(graph, cache, a, b)
        if path is None:
            return math.inf, None
        total += length
        full_path.extend(path if not full_path else path[1:])
        if avoid_reuse:
            used_edges.update(_edge_id(u, v) for u, v in zip(path, path[1:]))
    return total, full_path


def _estimate_path_m(start, end, seq):
    """Odhad delky trasy. Waypointy maji polomer (trasa se tile dotkne u okraje),
    start a cil jsou body - jinak odhad systematicky nadhodnocuje."""
    points = [(start, 0.0)]
    points += [((item["lat"], item["lon"]), TILE_EFFECTIVE_RADIUS_M) for item in seq]
    points.append((end, 0.0))

    straight = 0.0
    for (point, radius), (next_point, next_radius) in zip(points, points[1:]):
        gap = haversine_m(*point, *next_point) - radius - next_radius
        straight += max(gap, 0.0)
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


def _filler_candidates(start, end, max_m, used_tiles):
    """Tiles v dosahu bez ohledu na prinos - slouzi jen k dotazeni delky trasy,
    kdyz doporucene tiles nestaci na spodni hranici tolerance."""
    center_x, center_y = lon_lat_tile(start[1], start[0])
    span = int(max_m / 2 / 1200) + 2
    fillers = []
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            tile = (center_x + dx, center_y + dy)
            if tile in used_tiles:
                continue
            lat, lon = tile_center(tile)
            if _reachable(lat, lon, start, end, max_m):
                fillers.append({"tile": tile, "score": 0.0, "lat": lat, "lon": lon})
    return fillers


def _extend_to_window(details_for, start, end, best, min_m, max_m):
    """Prodluzuje trasu pres dalsi tiles v dosahu, dokud nedosahne okna delky.
    Vybira vzdy ten tile, se kterym odhad delky nejlepe trefi stred okna."""
    target_m = (min_m + max_m) / 2
    sequence = list(best["sequence"])
    fillers = _filler_candidates(start, end, max_m, {item["tile"] for item in sequence})
    current = best

    for _ in range(MAX_FILL_ROUNDS):
        if len(sequence) >= MAX_WAYPOINTS or not fillers:
            break

        choice = None
        for filler in fillers:
            for position in range(len(sequence) + 1):
                trial = sequence[:position] + [filler] + sequence[position:]
                estimate = _estimate_path_m(start, end, trial)
                if estimate <= max_m:
                    distance = abs(estimate - target_m)
                    if choice is None or distance < choice[0]:
                        choice = (distance, trial, filler)
        if choice is None:
            break

        fillers.remove(choice[2])
        details = details_for(choice[1])
        if not details or details["length_m"] <= current["length_m"]:
            continue

        current, sequence = details, choice[1]
        if current["in_window"]:
            break

    return current


def _route_details(graph, leg_cache, node_index, start_node, sequence, min_m, max_m, context,
                   end_node=None, avoid_reuse=False):
    """Exaktni trasa pro sekvenci waypointu + spolecny prinos protnutych tiles.
    Pri prekroceni max_m odpada nejslabsi waypoint. avoid_reuse penalizuje
    opakovany pruchod stejnou ulici."""
    from scoring import evaluate_tile_set

    sequence = list(sequence)
    start_point = (graph.nodes[start_node]["y"], graph.nodes[start_node]["x"])
    finish = start_point if end_node is None else (
        graph.nodes[end_node]["y"], graph.nodes[end_node]["x"]
    )

    while True:
        # Waypoint = uzel uvnitr tile nejmene zajizdejici z predchoziho bodu
        # k nasledujicimu cili (nasledujici tile zatim zastupuje jeho stred).
        waypoint_nodes = []
        previous = start_point
        for position, item in enumerate(sequence):
            following = (
                (sequence[position + 1]["lat"], sequence[position + 1]["lon"])
                if position + 1 < len(sequence) else finish
            )
            node = _pick_waypoint_node(graph, node_index, item["tile"], previous, following)
            waypoint_nodes.append(node)
            previous = (graph.nodes[node]["y"], graph.nodes[node]["x"])

        length_m, node_path = _exact_loop(
            graph, leg_cache, start_node, waypoint_nodes, end_node, avoid_reuse
        )
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
        "node_path": node_path,
        "coordinates": coordinates,
        "tiles_crossed": sorted(crossed),
        "benefit": evaluate_tile_set(crossed, context),
        "in_window": min_m <= length_m <= max_m,
        "repeated_m": _repeated_m(graph, node_path),
    }


def _variant_score(details):
    """Cilova funkce trasy: prinos snizeny o PODIL opakovanych ulic.

    Podil (ne absolutni km) proto, aby penalizace netrestala delku trasy:
    s absolutni penalizaci byl vzdy nejvyhodnejsi nejkratsi pripustny okruh.
    Nasobeni prinosem drzi meritko - u velkych i malych prinosu stejny vztah."""
    length_m = details["length_m"]
    ratio = details["repeated_m"] / length_m if length_m > 0 else 0.0
    return details["benefit"]["total"] * (1 - REPEAT_PENALTY_FRACTION * min(ratio, 1.0))


def _variant_key(details):
    return (details["in_window"], _variant_score(details), -details["length_m"])


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

    def details_for(sequence, avoid_reuse=False):
        return _route_details(
            graph, leg_cache, node_index, start_node, sequence, min_m, max_m, context, end_node,
            avoid_reuse=avoid_reuse,
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

    # Nizkoopakovaci varianty patri do portfolia, ne az do finalizace vitezze:
    # nejlepsi seedy se prepocitaji i s vyhybanim opakovanym ulicim a souteri
    # rovnocenne. (Jinak vyhraje trasa, ktera prinos nasbirala prave opakovanim,
    # a jeji "opravena" verze uz se neprosadi.)
    for details in sorted(variants, key=_variant_key, reverse=True)[:AVOID_VARIANTS]:
        # Vyhybani je drahe (hledani bez cache) - ma smysl jen tam, kde je co
        # zlepsovat; varianty s minimalnim opakovanim se preskakuji.
        if details["repeated_m"] <= AVOID_MIN_RATIO * details["length_m"]:
            continue
        refined = details_for(details["sequence"], avoid_reuse=True)
        if refined:
            variants.append(refined)

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

    # Kratsi trasa nez zadane okno: dotahni delku pres dalsi tiles v dosahu.
    if not best["in_window"] and best["length_m"] < min_m:
        best = _extend_to_window(details_for, start, end, best, min_m, max_m)

    # Finalni prepocet vitezne trasy s vyhybanim - pokryva vitezze, ktery vzesel
    # az z kola vylepsovani (varianty z portfolia uz svou avoid verzi maji).
    if best["sequence"] and best["repeated_m"] > 0:
        refined = details_for(best["sequence"], avoid_reuse=True)
        if refined and _variant_key(refined) > _variant_key(best):
            best = refined

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
        # itinerar se sklada az pro vitezneho kandidata (dohledavani nazvu ulic
        # je drahe, pro kazdou variantu by se nevyplatilo)
        "directions": route_directions(graph, best["node_path"]),
        "benefit": best["benefit"],
        "repeated_km": round(best["repeated_m"] / 1000, 2),
        "variants_compared": len(variants),
    }


WAY_LABELS = {
    "cycleway": "cyklostezka",
    "path": "pesina",
    "track": "polni cesta",
    "bridleway": "pesina",
    "pedestrian": "pesi zona",
    "footway": "chodnik",
    "steps": "schody",
    "living_street": "obytna zona",
    "residential": "ulice",
    "service": "ucelova cesta",
    "unclassified": "silnicka",
    "tertiary": "silnice",
    "secondary": "silnice",
    "primary": "hlavni silnice",
    "trunk": "hlavni silnice",
}
# Prahy slucovani kroku itinerare: pojmenovany usek nese informaci, takze
# prezije i kratsi; bezejmenne prebehy se slucuji drive.
STEP_MIN_M = 250.0
STEP_MIN_NAMED_M = 100.0
STREET_MATCH_MAX_M = 40.0   # chodnik "podel ulice" - max odstup od pojmenovane cesty
STREET_MATCH_MAX_DEG = 35.0  # ... a max odchylka smeru (aby slo o soubeznou cestu)
# Krizovane orientacni body: vyznamna ulice (a pozdeji zeleznice/voda) blizko
# uzlu trasy, kolma na smer behu = "krizis X".
LANDMARK_STREET_CLASSES = {
    "tertiary", "tertiary_link", "secondary", "secondary_link",
    "primary", "primary_link", "trunk", "trunk_link",
}
LANDMARK_MAX_M = 35.0
LANDMARK_CROSS_MIN_DEG = 45.0  # min odchylka smeru, aby slo o krizeni, ne soubeh


def _tag(edge, key):
    value = edge.get(key)
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _bearing(graph, u, v):
    lat1, lon1 = graph.nodes[u]["y"], graph.nodes[u]["x"]
    lat2, lon2 = graph.nodes[v]["y"], graph.nodes[v]["x"]
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _turn_word(previous_bearing, next_bearing):
    if previous_bearing is None:
        return None
    delta = (next_bearing - previous_bearing + 540) % 360 - 180
    if abs(delta) < 35:
        return None
    if abs(delta) > 150:
        return "zpet"
    return "vpravo" if delta > 0 else "vlevo"


_COMPASS = ["sever", "severovychod", "vychod", "jihovychod",
            "jih", "jihozapad", "zapad", "severozapad"]


def _compass(bearing):
    return _COMPASS[int((bearing % 360) / 45 + 0.5) % 8]


def _smoothed_bearing(graph, nodes, from_start, window_m=45.0):
    """Smer trasy na zacatku (from_start=True) nebo konci useku, vyhlazeny pres
    prvnich/poslednich ~window_m. Jednotliva hrana v parku/klikate ceste dava
    zkresleny azimut -> vlevo/vpravo pak vychazi spatne."""
    ordered = nodes if from_start else list(reversed(nodes))
    accumulated = 0.0
    anchor = ordered[0]
    end = ordered[-1]
    for node in ordered[1:]:
        end = node
        accumulated += haversine_m(
            graph.nodes[anchor]["y"], graph.nodes[anchor]["x"],
            graph.nodes[node]["y"], graph.nodes[node]["x"],
        )
        if accumulated >= window_m:
            break
    return _bearing(graph, anchor, end) if from_start else _bearing(graph, end, anchor)


def _crossed_street(graph, node):
    """Nazev vyznamne ulice, kterou trasa v danem uzlu krizi (je blizko a kolma
    ke smeru trasy) - orientacni bod pro tahak. Pojmenovane ulice pochazeji z
    indexu ulozeneho na grafu (enrich_streets)."""
    import numpy as np

    segments = graph.graph.get("street_segments")
    if segments is None:
        return None
    lats, lons, bearings, names, major = segments
    if not len(names) or not major.any():
        return None

    lat, lon = graph.nodes[node]["y"], graph.nodes[node]["x"]
    coslat = math.cos(math.radians(lat))
    meters = np.hypot((lats - lat) * 111320.0, (lons - lon) * 111320.0 * coslat)
    close = np.flatnonzero((meters <= LANDMARK_MAX_M) & major)
    if not len(close):
        return None

    neighbours = list(graph.successors(node)) + list(graph.predecessors(node))
    if not neighbours:
        return None
    own = _bearing(graph, node, neighbours[0]) % 180

    diff = np.abs(bearings[close] - own)
    diff = np.minimum(diff, 180 - diff)
    crossing = close[diff >= LANDMARK_CROSS_MIN_DEG]
    if not len(crossing):
        return None
    return str(names[int(crossing[np.argmin(meters[crossing])])])


def _segment_name(graph, u, v):
    """(nazev useku nebo None, typ cesty) - jen popis samotneho useku.
    Chodniky bez jmena maji ulici doplnenou v atributu along_street
    (enrich_streets)."""
    edge = _best_edge(graph, u, v)
    highway = _tag(edge, "highway")
    kind = WAY_LABELS.get(highway, "cesta")

    name = _tag(edge, "name")
    if name:
        return name, kind
    along = edge.get("along_street")
    if along:
        # u chodniku staci nazev ulice, beh po chodniku je vychozi predpoklad
        return (along if highway in FOOTLIKE_HIGHWAYS else f"{kind} podel {along}"), kind
    ref = _tag(edge, "ref")
    if ref:
        return f"{kind} {ref}", kind
    return None, kind


def _vote_step_name(step):
    """Nazev sloucneho kroku = ta pojmenovana ulice, ktera pokryva nejvic jeho
    delky (jednotlive hrany hlasuji svou delkou). Robustnejsi nez nazev prvni
    hrany - kdyz vetsina kroku vede podel jedne ulice, vyhraje, i kdyz par
    krajnich hran match nema."""
    votes = defaultdict(float)
    for name, length in step["names"]:
        if name:
            votes[name] += length
    if votes:
        best_name, covered = max(votes.items(), key=lambda item: item[1])
        if covered >= 0.4 * step["m"]:
            return best_name
    return None


def route_directions(graph, node_path):
    """Itinerar behu: useky se stejnym popisem slouceny, s kumulativni
    vzdalenosti, smerem zatoceni a orientacnimi body (krizene vyznamne ulice).
    Slouzi jako tahak na trasu."""
    from collections import Counter

    if len(node_path) < 2:
        return []

    steps = []
    for u, v in zip(node_path, node_path[1:]):
        edge = _best_edge(graph, u, v)
        name, kind = _segment_name(graph, u, v)
        label = name or kind
        length = float(edge["length"])
        bridge = bool(_tag(edge, "bridge")) and _tag(edge, "bridge") != "no"
        steps_here = _tag(edge, "highway") == "steps"

        if steps and steps[-1]["label"] == label:
            steps[-1]["m"] += length
            steps[-1]["bridge"] = steps[-1]["bridge"] or bridge
            steps[-1]["steps"] = steps[-1]["steps"] or steps_here
            steps[-1]["names"].append((name, length))
            steps[-1]["nodes"].append(v)
        else:
            steps.append({
                "label": label, "kind": kind, "named": bool(name), "m": length,
                "bridge": bridge, "steps": steps_here,
                "names": [(name, length)], "nodes": [u, v],
            })

    # Kratke useky splynou se sousedem: trasa casto prebehne par metru po
    # souběžné ulici a bez slouceni by tahak mel stovky nepouzitelnych kroku.
    # Ubira se vzdy nejkratsi krok, popis prebira delsi soused.
    def too_short(step):
        return step["m"] < (STEP_MIN_NAMED_M if step["named"] else STEP_MIN_M)

    def absorb(keeper, gone, append):
        keeper["m"] += gone["m"]
        keeper["bridge"] = keeper["bridge"] or gone["bridge"]
        keeper["steps"] = keeper["steps"] or gone["steps"]
        keeper["names"] += gone["names"]
        if append:
            keeper["nodes"] += gone["nodes"][1:]
        else:
            keeper["nodes"] = gone["nodes"][:-1] + keeper["nodes"]

    while len(steps) > 1:
        candidates = [index for index, step in enumerate(steps) if too_short(step)]
        if not candidates:
            break
        shortest = min(candidates, key=lambda index: steps[index]["m"])

        if shortest == 0:
            target = 1
        elif shortest == len(steps) - 1:
            target = shortest - 1
        else:
            target = shortest - 1 if steps[shortest - 1]["m"] >= steps[shortest + 1]["m"] else shortest + 1

        absorb(steps[target], steps[shortest], append=target < shortest)
        steps.pop(shortest)

    # Bezejmenne kroky jeste zkus pojmenovat hlasovanim hran (vetsi pokryti nez
    # nazev prvni hrany); pak sluc sousedy, kterym vysel stejny nazev.
    for step in steps:
        if not step["named"]:
            voted = _vote_step_name(step)
            if voted:
                step["label"] = voted
                step["named"] = True

    merged = []
    for step in steps:
        if merged and merged[-1]["label"] == step["label"]:
            absorb(merged[-1], step, append=True)
        else:
            merged.append(step)

    # Kumulativni vzdalenost k jednotlivym uzlum (pro km u krizeni).
    node_at = {}
    running = 0.0
    for a, b in zip(node_path, node_path[1:]):
        node_at[a] = node_at.get(a, running)
        running += haversine_m(graph.nodes[a]["y"], graph.nodes[a]["x"],
                               graph.nodes[b]["y"], graph.nodes[b]["x"])
    node_at[node_path[-1]] = running

    labels = [step["label"] for step in merged]
    directions = []
    previous_out = None
    cumulative_m = 0.0
    for index, step in enumerate(merged):
        bearing_in = _smoothed_bearing(graph, step["nodes"], from_start=True)
        # krizeni s km; vynech ulici, po ktere prave bezime nebo hned pobezime
        neighbour_labels = {step["label"]}
        if index > 0:
            neighbour_labels.add(labels[index - 1])
        if index + 1 < len(labels):
            neighbour_labels.add(labels[index + 1])
        crossings = []
        seen = set()
        for node in step["nodes"][1:-1]:
            crossed = _crossed_street(graph, node)
            if crossed and crossed not in neighbour_labels and crossed not in seen:
                seen.add(crossed)
                crossings.append({"name": crossed, "at_km": round(node_at.get(node, cumulative_m) / 1000, 2)})

        turn = _turn_word(previous_out, bearing_in)
        if turn is None and index > 0:
            turn = "rovne"  # zmena ulice bez zatoceni
        directions.append({
            "at_km": round(cumulative_m / 1000, 2),
            "km": round(step["m"] / 1000, 2),
            "label": step["label"],
            "kind": step["kind"],
            "turn": turn,
            "start_heading": _compass(bearing_in) if index == 0 else None,
            "bridge": step["bridge"],
            "steps": step["steps"],
            "crossings": crossings,
        })
        cumulative_m += step["m"]
        previous_out = _smoothed_bearing(graph, step["nodes"], from_start=False)
    return directions


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
