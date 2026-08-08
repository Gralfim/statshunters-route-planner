"""Vyber trasy: ktere dlazdice navstivit a v jakem poradi.

Kombinatoricka vrstva nad grafem. Nestavi jednu trasu, ale PORTFOLIO variant
(rank-greedy seed, seedy kolem skupin sousedicich kandidatu, seedy na
dokompletovani max square, nizkoopakovaci prepocty) a kazdou exaktne prepocita.
Vitezi ta s nejvyssim spolecnym prinosem vsech protnutych dlazdic - zisky
square/cluster nejsou aditivni pres jednotlive dlazdice, takze se musi pocitat
nad celou mnozinou najednou.

Odhady delky (vzdusna cara x DETOUR_FACTOR) slouzi jen k razeni a orezu
kandidatu; o vysledku vzdy rozhodne exaktni prepocet po grafu.
"""
import math
from pathlib import Path

from geo import haversine_m, tile_center
from geojson import lon_lat_tile, tile_lon_lat
from itinerary import route_directions
from runcost import (along_major_m, best_edge, corridor_m, edge_id, path_length_m,
                     repeated_m, route_weight, trail_m)
from waygraph import load_walk_graph, nearest_node, node_index, path_coordinates

ROOT = Path(__file__).resolve().parents[1]

DETOUR_FACTOR = 1.35
MAX_WAYPOINTS = 8
MAX_CANDIDATES = 60
MAX_GROUP_SEEDS = 8
MAX_SQUARE_SEEDS = 4
MAX_SQUARE_MISSING = 4
IMPROVE_ROUNDS = 2
IMPROVE_MOVES_PER_ROUND = 10
# Cilova funkce trasy je prinos POSTUPNE SNIZOVANY ctyrmi merkami kvality:
#   skore = prinos x (1 - CORRIDOR x podil delky v opakovanem koridoru)
#                   x (1 - quiet_weight x podil delky podel vyznamnych ulic)
#                   x (1 - quiet_weight x TRAIL x podil delky MIMO znacene trasy)
#                   x (1 - LENGTH x odchylka delky / tolerance)
# Vsechny jsou PODILOVE a nasobi prinos, takze prinos zustava dominantni ("prinos
# king") a merky rozhoduji mezi jinak srovnatelnymi trasami. Absolutni penalizace
# by delaly z nejkratsiho pripustneho okruhu vzdy vitezze.
#
# Opakovani se meri KORIDOREM (runcost.corridor_m), ne shodou hran. Trasa, ktera
# jde udolim tam po jedne strane a zpet po druhe, ma opakovanych hran nula, a
# pritom je porad na tomtez miste - `repeated_m` ten jev nezachyti (mereno pri
# vaze klidu 1,0: opakovani 0-1 % z Karlova nam. i ze Zahradniho Mesta).
# Koridor je nadmnozina: u presneho opakovani vyjde zhruba dvojnasobny, protoze
# pocita oba pruchody. Proto je zlomek POLOVICNI proti drivejsimu REPEAT (0,5) -
# na presne se opakujici trase trestá stejne jako predtim, navic ale vidi
# soubezne vedeni. Penalizovat obojí by tentyz metr pocitalo dvakrat.
CORRIDOR_PENALTY_FRACTION = 0.25
# Podil delky vedouci podel vyznamnych ulic. Bez tohoto clenu neumel planovac
# porovnat "hodne dlazdic po magistrale" s "min dlazdic po klidu" - vzal vzdy
# prvni a druhou variantu ani nepostavil. Vaha je RUNTIME parametr (posuvnik
# v UI, vychozi z configu), protoze je to preference, ne fyzika.
DEFAULT_QUIET_WEIGHT = 0.6
# Znacene trasy jsou druha strana teze preference: "nevede podel magistraly" je
# jen nepritomnost spatneho, kdezto znacka vede udolim, parkem nebo podel vody.
# Bez tohoto clenu na ni cilova funkce nebrala ohled vubec - mereno na okruhu
# 15+-3 km z Karlova nam.: v portfoliu lezela trasa se 72 % delky po znackach a
# prohravala s trasou se 47 %, protoze ji nic neodmenovalo. Penalizuje se podil
# MIMO znacene trasy, aby skore nikdy neprerostlo prinos.
#
# Kalibrace: pri plne vaze klidu ma zlepseni o 25 procentnich bodu podilu po
# znackach vyvazit zhruba 15% rozdil v prinosu (presne ten pripad, kvuli kteremu
# clen vznikl). Nizsi hodnota by ho neprehodila.
TRAIL_PENALTY_FRACTION = 0.5
# Odchylka delky od CILOVE hodnoty, normovana toleranci: 0 presne na cili, 1 na
# hranici okna. Tolerance byla zavedena jen jako obalka splnitelnosti, ale bez
# tohoto clenu se z ni stala preference - delsi trasa protne vic dlazdic, takze
# vitezily trasy u horni hranice. Ted je horni hranice porad pripustna, jen musi
# svou delku vyplatit vyssim prinosem.
LENGTH_PENALTY_FRACTION = 0.35
# Kolik nejlepsich variant se navic prepocita s vyhybanim opakovanym ulicim
# (nizkoopakovaci varianty maji byt v portfoliu, ne az finalizaci vitezze).
AVOID_VARIANTS = 3
AVOID_MIN_RATIO = 0.03  # pod timto podilem opakovani se avoid varianta nepocita
# Klidne varianty: nejlepsi seedy se prepocitaji s prirazkou cestam podel
# vyznamnych ulic. Stejny vzorec jako u opakovani - varianta patri do portfolia,
# aby soutezila rovnocenne, ne aby se vitez "opravoval" na konci.
QUIET_VARIANTS = 2
# Nekolik urovni prirazky, VZDY vsechny - portfolio ma pak spektrum klidu a
# posuvnik si z nej vybira. Merene chovani na okruhu z Karlova nam.: x1,6 dava
# 1,5 % delky podel vyznamnych ulic, x5 uz 0,8 %, ale delsi trasu.
#
# Urovne zamerne NEzavisi na posuvniku. Kdyz prirazka skalovala s vahou, mel
# planovac pri kazde vaze jen jednu klidnou variantu; silnejsi prirazka delala
# delsi trasu, tu potrestala penalizace odchylky delky a vyhrala zakladni
# varianta - posuvnik pak vychazel NEMONOTONNE (vaha 0,6 dala 8,6 % podel
# vyznamnych ulic, zatimco 0,2 jen 0,9 %). S pevnou sadou kandidatu muze vyssi
# vaha poradi mezi dvema trasami prehodit uz jen ve prospech te klidnejsi.
#
# Kazda uroven je (prirazka cestam podel vyznamnych ulic, sleva znacenym trasam):
# mirna se jen vyhyba, silna aktivne hleda znacky (udoli, parky, podel vody).
QUIET_LEG_PROFILES = ((1.6, 1.0), (5.0, 0.5))
# Kolik tiles navic smi trasa pobrat, aby se dostala na spodni hranici tolerance
# (waypointy mirici na okraj tile zkracuji trasu - delku je pak treba dotahnout).
MAX_FILL_ROUNDS = 5
# Kolikrat se smi z prilis dlouhe trasy vypustit waypoint, aby se priblizila
# cilove delce (protejsek MAX_FILL_ROUNDS), a kolik kandidatu na vypusteni se
# v kazdem kole zkusi. Soucin je pocet exaktnich prepoctu navic.
MAX_SHRINK_ROUNDS = 2
SHRINK_CANDIDATES = 3

# Kolik variant se nabidne k vyberu a jak moc se od sebe musi lisit. Bez mery
# odlisnosti by uzivatel dostal trikrat skoro tutez trasu: portfolio obsahuje
# hodne prepoctu TEZE sekvence (vyhybani opakovanym ulicim, klidne varianty),
# ktere se od sebe lisi jen par sty metry. Meri se podilem spolecnych hran.
MAX_VARIANTS = 3
MAX_VARIANT_OVERLAP = 0.6
# Waypoint se umistuje dovnitr tile, ne do jeho stredu - ale s rezervou od
# hranice, aby tile zustal navstiveny i pri chybe GPS nebo navigace pri behu.
TILE_MARGIN_M = 75.0
# Efektivni "polomer" tile pro odhady delky: trasa miri na okraj bezpecne zony,
# ne do stredu (tile ma v nasich sirkach ~1570 m, pulka 785 m minus rezerva).
# Bez teto korekce odhady nadhodnocovaly delku 1,5-4x a greedy prestal pridavat
# tiles drive, nez trasa dosahla spodni hranice tolerance.
TILE_EFFECTIVE_RADIUS_M = 700.0


_TILE_NODES_CACHE = {}


def _tile_interior_nodes(graph, index, tile):
    """Uzly lezici uvnitr tile s rezervou TILE_MARGIN_M od hranice.

    Rezerva je pojistka proti chybe GPS/navigace: trasa jen tecna k hranici by
    tile pri par metrech odchylky nemusela zapocitat. Kdyz v bezpecne zone zadna
    cesta neni, ustupuje se na cely tile a nakonec na stred (fallbacky)."""
    import numpy as np

    key = (id(graph), tile)
    if key not in _TILE_NODES_CACHE:
        nodes, lats, lons = index
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
        selected = np.flatnonzero(safe)
        if not len(selected):
            selected = np.flatnonzero(inside)
        _TILE_NODES_CACHE[key] = ([nodes[i] for i in selected], lats[selected], lons[selected])
    return _TILE_NODES_CACHE[key]


def _pick_waypoint_node(graph, index, tile, previous, following):
    """Uzel v tile, ktery nejmene zajizdi: minimalizuje vzdalenost od
    predchoziho bodu trasy k nemu a dal k nasledujicimu cili. Trasa se tak tile
    dotkne tam, kudy stejne vede, misto zajizdky do geometrickeho stredu."""
    import numpy as np

    nodes, lats, lons = _tile_interior_nodes(graph, index, tile)
    if not len(nodes):
        return nearest_node(index, *tile_center(tile))

    coslat = math.cos(math.radians(previous[0]))
    to_previous = np.hypot(lats - previous[0], (lons - previous[1]) * coslat)
    to_following = np.hypot(lats - following[0], (lons - following[1]) * coslat)
    return nodes[int(np.argmin(to_previous + to_following))]


def _leg(graph, cache, a, b):
    """Nejlepsi usek podle run_cost (preference typu cest); vraci REALNOU delku."""
    import networkx as nx

    if (a, b) not in cache:
        try:
            _cost, path = nx.bidirectional_dijkstra(graph, a, b, weight="run_cost")
            length = path_length_m(graph, path)
        except nx.NetworkXNoPath:
            length, path = math.inf, None
        cache[(a, b)] = (length, path)
    return cache[(a, b)]


def _leg_weighted(graph, a, b, used_edges=None, quiet_factor=1.0, trail_factor=1.0):
    """Usek a->b s upravenymi cenami: prirazka uz pouzitym hranam (used_edges)
    a cestam podel vyznamnych ulic (quiet_factor), sleva znacenym trasam
    (trail_factor). Nema cache - hleda se pro kazdou variantu."""
    import networkx as nx

    try:
        _cost, path = nx.bidirectional_dijkstra(
            graph, a, b, weight=route_weight(used_edges, quiet_factor, trail_factor)
        )
    except nx.NetworkXNoPath:
        return math.inf, None
    return path_length_m(graph, path), path


def _trim_spurs(graph, node_tiles, node_path):
    """Zkrati slepe ocasky (usek do tile a zpet stejnou cestou) na nejkratsi
    delku, ktera zachova protnute tiles - VCETNE bezpecne hloubky pruniku.

    Puvodne stacilo, aby tile pokryval jakykoli jiny uzel trasy. Jenze prave
    spicka ocasku byla to, kvuli cemu se do dlazdice zajizdelo: ores nechal
    trasu, ktera dlazdici jen skrabne. Mereno na referencni trase - cilova
    dlazdice mela v bezpecne zone 2 362 uzlu (az 778 m hluboko), ale trasa ji
    prosla nejhloub 49 m, tedy pod TILE_MARGIN_M, ktery ma chranit proti chybe
    GPS. Uzel se proto nesmi odriznout, kdyz je posledni dost hluboky ve svem
    tile."""
    from collections import Counter

    from itinerary import _tile_depth_m

    def depth(node):
        return _tile_depth_m(graph.nodes[node]["y"], graph.nodes[node]["x"], node_tiles[node])

    path = list(node_path)
    counts = Counter(node_tiles[node] for node in path)
    deep = Counter(node_tiles[node] for node in path if depth(node) >= TILE_MARGIN_M)

    def may_drop(node):
        tile = node_tiles[node]
        if counts[tile] <= 1:
            return False
        return depth(node) < TILE_MARGIN_M or deep[tile] > 1

    def drop(node):
        tile = node_tiles[node]
        counts[tile] -= 1
        if depth(node) >= TILE_MARGIN_M:
            deep[tile] -= 1

    i = 1
    while i < len(path) - 1:
        if path[i - 1] == path[i + 1] and may_drop(path[i]) and may_drop(path[i + 1]):
            drop(path[i])
            drop(path[i + 1])
            del path[i:i + 2]
            i = max(i - 1, 1)
        else:
            i += 1
    return path


def plan_walk(graph, from_lat, from_lon, to_lat, to_lon):
    """Pesi/bezecky presun mezi dvema body po stejnem grafu jako behy."""
    index = node_index(graph)
    node_a = nearest_node(index, from_lat, from_lon)
    node_b = nearest_node(index, to_lat, to_lon)
    length_m, path = _leg(graph, {}, node_a, node_b)
    if path is None:
        raise RuntimeError("No walkable path between the points")
    return {
        "km": round(float(length_m) / 1000, 2),
        "coordinates": path_coordinates(graph, path),
    }


def _exact_loop(graph, cache, start_node, waypoint_nodes, end_node=None,
                avoid_reuse=False, quiet_factor=1.0, trail_factor=1.0):
    order = [start_node] + waypoint_nodes + [end_node if end_node is not None else start_node]
    total = 0.0
    full_path = []
    used_edges = set()
    for a, b in zip(order, order[1:]):
        if avoid_reuse or quiet_factor != 1.0 or trail_factor != 1.0:
            length, path = _leg_weighted(
                graph, a, b, used_edges if avoid_reuse else None, quiet_factor, trail_factor
            )
        else:
            length, path = _leg(graph, cache, a, b)
        if path is None:
            return math.inf, None
        total += length
        full_path.extend(path if not full_path else path[1:])
        if avoid_reuse:
            used_edges.update(edge_id(u, v) for u, v in zip(path, path[1:]))
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


def candidate_groups(within):
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


def _greedy_fill(start, end, sequence, pool, limit_m):
    """Cheapest insertion na odhadech: doplnuje kandidaty z poolu, dokud se
    odhad delky vejde pod limit_m.

    limit_m je uroven naplnenosti, ne horni hranice tolerance. Portfolio se
    stavi na DVOU urovnich (cilova delka a horni hranice okna) - kdyz se plnilo
    jen po max_m, zadny kandidat blizko cile nevznikl a cilova funkce mohla
    vybirat jen mezi dlouhymi trasami."""
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
            if estimate <= limit_m and (best is None or estimate < best[0]):
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


def _shrink_toward_target(details_for, key, best, target_m):
    """Zkrati trasu k cilove delce vypustenim waypointu - dokud se skore zlepsuje.

    Zrcadlovy protejsek `_extend_to_window`. Pracuje se SKUTECNOU delkou, ne s
    odhadem: odhad (`_estimate_path_m`) systematicky podstreluje, takze i
    sekvence naplnena "jen po cil" vyjde po exaktnim prepoctu nad cilem. Delku
    proto nejde uridit pri stavbe sekvence, jen zpetnou vazbou z prepoctu.

    Zkousi se vypustit nekolik nejslabsich waypointu a vezme se ten nejlepsi
    vysledek - vypustit ten s nejnizsim skore nestaci, protoze delku trasy
    urcuje poloha dlazdic, ne jejich pocet (mereno: vypusteni nejslabsiho
    waypointu trasu o 90 m PRODLOUZILO). Jestli se zkraceni vyplati, rozhoduje
    cilova funkce: kratsi trasa ma mensi prinos, ale i mensi odchylku delky.
    """
    current = best
    for _ in range(MAX_SHRINK_ROUNDS):
        sequence = current["sequence"]
        if len(sequence) <= 1 or current["length_m"] <= target_m:
            break

        weakest = sorted(sequence, key=lambda item: item["score"])[:SHRINK_CANDIDATES]
        trials = []
        for dropped in weakest:
            details = details_for([item for item in sequence if item is not dropped])
            if details:
                trials.append(details)
        if not trials:
            break

        better = max(trials, key=key)
        if key(better) <= key(current):
            break
        current = better
    return current


def _route_details(graph, leg_cache, index, start_node, sequence, min_m, max_m, context,
                   end_node=None, avoid_reuse=False, quiet_factor=1.0, trail_factor=1.0):
    """Exaktni trasa pro sekvenci waypointu + spolecny prinos protnutych tiles.
    Pri prekroceni max_m odpada nejslabsi waypoint. avoid_reuse penalizuje
    opakovany pruchod stejnou ulici, quiet_factor cesty podel vyznamnych ulic
    a trail_factor zvyhodnuje znacene trasy."""
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
            node = _pick_waypoint_node(graph, index, item["tile"], previous, following)
            waypoint_nodes.append(node)
            previous = (graph.nodes[node]["y"], graph.nodes[node]["x"])

        length_m, node_path = _exact_loop(
            graph, leg_cache, start_node, waypoint_nodes, end_node,
            avoid_reuse, quiet_factor, trail_factor,
        )
        if node_path is None:
            return None
        node_tiles = {
            node: lon_lat_tile(graph.nodes[node]["x"], graph.nodes[node]["y"])
            for node in node_path
        }
        node_path = _trim_spurs(graph, node_tiles, node_path)
        length_m = path_length_m(graph, node_path)
        if length_m <= max_m or not sequence:
            break
        weakest = min(sequence, key=lambda item: item["score"])
        sequence = [item for item in sequence if item is not weakest]

    coordinates = path_coordinates(graph, node_path)
    crossed = {lon_lat_tile(lon, lat) for lat, lon in coordinates}
    return {
        "sequence": sequence,
        "length_m": length_m,
        "node_path": node_path,
        "coordinates": coordinates,
        "tiles_crossed": sorted(crossed),
        "benefit": evaluate_tile_set(crossed, context),
        "in_window": min_m <= length_m <= max_m,
        "repeated_m": repeated_m(graph, node_path),
        "corridor_m": corridor_m(coordinates),
        "along_major_m": along_major_m(graph, node_path),
        "trail_m": trail_m(graph, node_path),
    }


def _variant_edges(details):
    path = details["node_path"]
    return {edge_id(u, v) for u, v in zip(path, path[1:])}


def _distinct_variants(variants, key, limit=MAX_VARIANTS):
    """Nejlepsi varianty, ktere se navzajem dost lisi (prvni je vitez).

    Bez tohoto filtru vraci portfolio nekolik prepoctu teze trasy - k vyberu
    maji smysl jen ty, ktere vedou doopravdy jinudy."""
    chosen, taken = [], []
    for details in sorted(variants, key=key, reverse=True):
        edges = _variant_edges(details)
        if not edges:
            continue
        if any(len(edges & other) / len(edges) > MAX_VARIANT_OVERLAP for other in taken):
            continue
        chosen.append(details)
        taken.append(edges)
        if len(chosen) >= limit:
            break
    return chosen


def _variant_score(details, target_m, tolerance_m, quiet_weight):
    """Cilova funkce trasy: prinos snizeny tremi podilovymi merkami kvality
    (opakovani ulic, vedeni podel vyznamnych ulic, odchylka delky od cile).

    Podily, ne absolutni hodnoty: s absolutni penalizaci byl vzdy nejvyhodnejsi
    nejkratsi pripustny okruh. Nasobeni prinosem drzi meritko - u velkych i
    malych prinosu stejny vztah."""
    length_m = details["length_m"]
    if length_m <= 0:
        return 0.0

    # starsi details bez merky (z cache nebo z testu) se nesmi rozbit
    corridor = min(details.get("corridor_m", 0.0) / length_m, 1.0)
    major = min(details["along_major_m"] / length_m, 1.0)
    off_trail = 1.0 - min(details.get("trail_m", 0.0) / length_m, 1.0)
    deviation = min(abs(length_m - target_m) / tolerance_m, 1.0) if tolerance_m > 0 else 0.0

    return (details["benefit"]["total"]
            * (1 - CORRIDOR_PENALTY_FRACTION * corridor)
            * (1 - quiet_weight * major)
            * (1 - quiet_weight * TRAIL_PENALTY_FRACTION * off_trail)
            * (1 - LENGTH_PENALTY_FRACTION * deviation))


def plan_tile_loop(graph, start_lat, start_lon, target_km, tolerance_km, candidates, context,
                   end_lat=None, end_lon=None, quiet_weight=None):
    """Beh v delce target +- tolerance s nejvetsim spolecnym prinosem.

    Okruh (end == start, vychozi), nebo z bodu do bodu (end_lat/end_lon).
    Porovnava varianty: rank-greedy seed + seedy kolem skupin sousednich kandidatu
    + seedy na dokompletovani square, kazdou exaktne prepocita a ohodnoti
    spolecnym prinosem VSECH protnutych tiles (evaluate_tile_set - zisky mnoziny,
    ne soucet skore) snizenym o merky kvality (_variant_score). Vitez se jeste
    zkousi vylepsit pridavanim kandidatu.

    quiet_weight 0..1 = jak silne se pocita podil delky podel vyznamnych ulic;
    0 znamena "jen sbirej dlazdice", 1 "co nejvic klidu".
    """
    min_m = (target_km - tolerance_km) * 1000
    max_m = (target_km + tolerance_km) * 1000
    target_m = target_km * 1000
    tolerance_m = tolerance_km * 1000
    if quiet_weight is None:
        quiet_weight = DEFAULT_QUIET_WEIGHT
    quiet_weight = min(max(float(quiet_weight), 0.0), 1.0)
    start = (start_lat, start_lon)
    end = (end_lat, end_lon) if end_lat is not None else start
    is_loop = end == start

    within = _within_reach(candidates, start, end, max_m)
    index = node_index(graph)
    start_node = nearest_node(index, start_lat, start_lon)
    end_node = None if is_loop else nearest_node(index, end[0], end[1])
    leg_cache = {}

    def details_for(sequence, avoid_reuse=False, quiet_factor=1.0, trail_factor=1.0):
        return _route_details(
            graph, leg_cache, index, start_node, sequence, min_m, max_m, context, end_node,
            avoid_reuse=avoid_reuse, quiet_factor=quiet_factor, trail_factor=trail_factor,
        )

    def variant_key(details):
        """Trasa v okne delky vzdy prebije trasu mimo nej; jinak rozhoduje skore
        a pri shode kratsi trasa."""
        return (details["in_window"],
                _variant_score(details, target_m, tolerance_m, quiet_weight),
                -details["length_m"])

    # Vyber skupin podle souctu skore je jen levny proxy pro poradi seedu;
    # rozhoduje az spolecny prinos exaktnich variant.
    groups = sorted(
        candidate_groups(within),
        key=lambda group: sum(member["score"] for member in group),
        reverse=True,
    )[:MAX_GROUP_SEEDS]

    variants = []
    seen_sequences = set()

    def add_variant(sequence, **kwargs):
        """Prida variantu do portfolia; tutez sekvenci se stejnym nastavenim
        nepocita dvakrat (exaktni prepocet je to drahe misto)."""
        key = (tuple(item["tile"] for item in sequence), tuple(sorted(kwargs.items())))
        if key in seen_sequences:
            return None
        seen_sequences.add(key)
        details = details_for(sequence, **kwargs)
        if details:
            variants.append(details)
        return details

    add_variant(_greedy_fill(start, end, [], within, max_m))

    for group in groups:
        seed = _greedy_fill(start, end, [], sorted(group, key=lambda m: -m["score"]), max_m)
        if not seed:
            continue
        add_variant(_greedy_fill(start, end, seed, within, max_m))

    for square_seed in _square_completion_seeds(within, context, start, end, max_m):
        seed = _greedy_fill(start, end, [], sorted(square_seed, key=lambda m: -m["score"]), max_m)
        if len(seed) < len(square_seed):
            continue
        add_variant(_greedy_fill(start, end, seed, within, max_m))

    # Nizkoopakovaci varianty patri do portfolia, ne az do finalizace vitezze:
    # nejlepsi seedy se prepocitaji i s vyhybanim opakovanym ulicim a souteri
    # rovnocenne. (Jinak vyhraje trasa, ktera prinos nasbirala prave opakovanim,
    # a jeji "opravena" verze uz se neprosadi.)
    for details in sorted(variants, key=variant_key, reverse=True)[:AVOID_VARIANTS]:
        # Vyhybani je drahe (hledani bez cache) - ma smysl jen tam, kde je co
        # zlepsovat; varianty s minimalnim opakovanim se preskakuji.
        if details["repeated_m"] <= AVOID_MIN_RATIO * details["length_m"]:
            continue
        add_variant(details["sequence"], avoid_reuse=True)

    # Klidne varianty stejnym vzorcem: tytez cilove dlazdice, ale usek se hleda
    # s prirazkou cestam podel vyznamnych ulic. Az takova varianta ukaze, kolik
    # klid opravdu stoji - odhadnout to z jedne trasy nejde. Pocitaji se i pri
    # nulove vaze, aby portfolio na posuvniku nezaviselo (viz QUIET_LEG_PROFILES).
    for details in sorted(variants, key=variant_key, reverse=True)[:QUIET_VARIANTS]:
        for quiet_factor, trail_factor in QUIET_LEG_PROFILES:
            add_variant(details["sequence"], avoid_reuse=True,
                        quiet_factor=quiet_factor, trail_factor=trail_factor)

    if not variants:
        raise RuntimeError("No walkable route found from the start point")

    best = max(variants, key=variant_key)

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
            if details and variant_key(details) > variant_key(best):
                best = details
                improved = True
        if not improved:
            break

    # Kratsi trasa nez zadane okno: dotahni delku pres dalsi tiles v dosahu.
    if not best["in_window"] and best["length_m"] < min_m:
        best = _extend_to_window(details_for, start, end, best, min_m, max_m)

    # Delsi nez cil: zkus ji zkratit k cilove delce. Rozhodne cilova funkce -
    # kratsi trasa ma mensi prinos, ale i mensi odchylku delky.
    if best["length_m"] > target_m:
        best = _shrink_toward_target(details_for, variant_key, best, target_m)

    # Finalni prepocet vitezne trasy s vyhybanim - pokryva vitezze, ktery vzesel
    # az z kola vylepsovani (varianty z portfolia uz svou avoid verzi maji).
    if best["sequence"] and best["repeated_m"] > 0:
        refined = details_for(best["sequence"], avoid_reuse=True)
        if refined and variant_key(refined) > variant_key(best):
            best = refined

    # Vitez patri do vyberu, i kdyz vzesel az z kol vylepsovani (ta do portfolia
    # nepridavaji).
    if all(details is not best for details in variants):
        variants.append(best)

    # Dlazdice, kvuli kterym se beh dela: ty, na ktere trasa mirila (waypointy),
    # plus vsechny doporucene, ktere cestou protne. Itinerar podle nich rekne,
    # kde a jak hluboko se sbira.
    recommended = {tuple(cand["tile"]) for cand in candidates}

    def output(details):
        length_m = details["length_m"] or 1.0
        waypoints = [item["tile"] for item in details["sequence"]]
        collected = [tile for tile in details["tiles_crossed"] if tile in recommended]
        return {
            "length_km": round(details["length_m"] / 1000, 2),
            "target_km": target_km,
            "tolerance_km": tolerance_km,
            "within_target": details["in_window"],
            "start": {"lat": start_lat, "lon": start_lon},
            "end": {"lat": end[0], "lon": end[1]},
            "is_loop": is_loop,
            "waypoint_tiles": waypoints,
            "tiles_crossed": details["tiles_crossed"],
            "coordinates": details["coordinates"],
            "directions": route_directions(graph, details["node_path"],
                                           target_tiles=collected,
                                           waypoint_tiles=waypoints,
                                           coordinates=details["coordinates"]),
            "benefit": details["benefit"],
            "repeated_km": round(details["repeated_m"] / 1000, 2),
            "corridor_km": round(details["corridor_m"] / 1000, 2),
            "corridor_share": round(details["corridor_m"] / length_m, 3),
            # merky kvality, podle kterych se trasa vybrala - v UI je videt, co
            # posuvnik "prinos <-> klid" udelal
            "along_major_km": round(details["along_major_m"] / 1000, 2),
            "along_major_share": round(details["along_major_m"] / length_m, 3),
            "trail_km": round(details["trail_m"] / 1000, 2),
            "trail_share": round(details["trail_m"] / length_m, 3),
            "quiet_weight": quiet_weight,
            "score": round(_variant_score(details, target_m, tolerance_m, quiet_weight), 3),
            "variants_compared": len(variants),
        }

    # Nabidka k vyberu: vitez + varianty, ktere vedou doopravdy jinudy. Itinerar
    # se sklada pro kazdou z nich (dohledavani nazvu ulic je drahe, proto jen pro
    # tech par nabidnutych, ne pro cele portfolio).
    chosen = _distinct_variants(variants, variant_key)
    result = output(best)
    result["variants"] = [output(details) for details in chosen if details is not best]
    return result


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
