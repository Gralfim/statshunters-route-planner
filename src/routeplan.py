"""Vyber trasy: ktere dlazdice navstivit a v jakem poradi.

Kombinatoricka vrstva nad grafem. Nestavi jednu trasu, ale PORTFOLIO variant
(posloupnosti z ruznych oblasti plnene podle hodnoty na kilometr, seedy na
dokompletovani max square, nizkoopakovaci a klidne prepocty) a kazdou exaktne
prepocita.
Vitezi ta s nejvyssim spolecnym prinosem vsech protnutych dlazdic - zisky
square/cluster nejsou aditivni pres jednotlive dlazdice, takze se musi pocitat
nad celou mnozinou najednou.

Odhady delky (vzdusna cara x DETOUR_FACTOR, kalibrovane pomerem zmerenym na
exaktnich prepoctech) slouzi jen ke stavbe posloupnosti; o vysledku vzdy
rozhodne exaktni prepocet po grafu.
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
MAX_SQUARE_SEEDS = 4
MAX_SQUARE_MISSING = 4
# Lokalni hledani kolem vitezze: pridat, vypustit, vymenit waypoint, u okruhu
# obratit smer. Kazdy tah je exaktni prepocet, proto strop. Drive se vitez
# zkousel jen prodluzovat - poradi a vymeny zustaly na nahode stavby
# posloupnosti (okruh z Barrandova: tytez dlazdice jednou jako okruh, jindy
# tam a zpet po stejne ceste, skore 276 vs 258).
LOCAL_SEARCH_EVALS = 24
LOCAL_SEARCH_MOVES = 3  # kolik nejslibnejsich tahu kazdeho druhu
# Vahy klidu, se kterymi se hleda - PEVNE, ne z posuvniku (stejny princip jako
# QUIET_LEG_PROFILES): portfolio pak na posuvniku nezavisi a ten jen vybira.
# Hledani s vahou z posuvniku prohledalo pro kazdou polohu jine okoli (pri
# plnem klidu vysla trasa s 15,3 % delky podel rusnych ulic, pri nulovem
# 13,9 %); jen s neutralni vahou zase portfolio nemelo klidne POSLOUPNOSTI,
# jen klidne prepocty tychz dlazdic, a obe krajni polohy daly tutez trasu.
SEARCH_QUIET_WEIGHTS = (0.0, 1.0)

# Jak se stavi posloupnosti waypointu (mereno na rucni trase Stodulky ->
# Barrandov, 08/2026, kterou planovac nenasel ani na grafu, kde ji umel projit):
#
# Pocatecni posloupnosti z RUZNYCH OBLASTI. Drive se vsechny stavely v poradi
# skore dlazdic, takze zacinaly u tychz ctyr dlazdic na severu (96,8 bodu) a jih
# (dlazdice po ~33, cenne az spolecne) se nezkusil ani jednou. Mrizka po
# REGION_TILES dlazdicich (~4,7 km) = ruzne smery behu.
REGION_TILES = 3
MAX_REGION_SEEDS = 6
# Plni se podle HODNOTY NA PRIDANY KILOMETR, ne podle poradi skore: posloupnost
# roste kolem sebe a nemicha protilehle smery (jedna posloupnost drive vedla
# sever i jih zaroven a exaktne mela 29 km pri odhadu 16,6). Dlazdice, ktere
# trasa protne skoro zadarmo, by mely nekonecny pomer - pridana delka se proto
# pocita aspon MIN_INSERT_M.
MIN_INSERT_M = 200.0
# Kazdy DALSI waypoint stoji ve skutecnosti ~1,4 km navic, ktere vzdusna cara
# nevidi (zajizdka do bezpecne zony dlazdice, oklika po siti mezi sousednimi
# dlazdicemi, kde odhad dava mezeru nula). Mereno na 68 posloupnostech ve trech
# oblastech (Stodulky -> Barrandov, okruh z Karlova nam., okruh ze Stodulek):
# skutecna delka = 0,8-1,0 x odhad + 1,40-1,58 km na waypoint; pomer
# skutecne/odhad byl u jednoho waypointu 0,9-1,0, u osmi 1,9-2,5. Bez tohoto
# clenu plnilo hledani osm waypointu do rozpoctu, do ktereho se vesly tri, a
# oprava pretecene trasy je pak musela vyhazet.
WAYPOINT_OVERHEAD_M = 1400.0
# Zbytkovou chybu odhadu (teren se lisi smer od smeru) meri pomer skutecne/
# odhadnute delky z exaktnich prepoctu teze planovaci ulohy. Posloupnost se
# plni k CILOVE delce (ne k horni hranici okna) delene timto pomerem.
INITIAL_CALIBRATION = 1.0
CALIBRATION_BOUNDS = (0.8, 2.0)
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


def _estimate_point(item):
    """Waypoint pro odhad: stred tile s polomerem (trasa se ho dotkne u okraje)."""
    return (item["lat"], item["lon"]), TILE_EFFECTIVE_RADIUS_M


def _estimate_gap_m(a, b):
    (point_a, radius_a), (point_b, radius_b) = a, b
    return DETOUR_FACTOR * max(haversine_m(*point_a, *point_b) - radius_a - radius_b, 0.0)


def _estimate_points(start, end, seq):
    return [(start, 0.0)] + [_estimate_point(item) for item in seq] + [(end, 0.0)]


def _estimate_path_m(start, end, seq):
    """Odhad delky trasy. Waypointy maji polomer (trasa se tile dotkne u okraje),
    start a cil jsou body - jinak odhad systematicky nadhodnocuje. Kazdy dalsi
    waypoint pridava WAYPOINT_OVERHEAD_M."""
    points = _estimate_points(start, end, seq)
    return (sum(_estimate_gap_m(a, b) for a, b in zip(points, points[1:]))
            + WAYPOINT_OVERHEAD_M * max(len(seq) - 1, 0))


def _reachable(lat, lon, start, end, max_m):
    """Bod je v dosahu, kdyz se objizdka start -> bod -> end vejde do rozpoctu
    (pro okruh start == end degeneruje na kruh)."""
    detour = haversine_m(start[0], start[1], lat, lon) + haversine_m(lat, lon, end[0], end[1])
    return detour <= max_m * 0.9


def _within_reach(candidates, start, end, max_m, context=None):
    """Kandidati v dosahu, serazeni podle HODNOTY PRO HLEDANI (`value`).

    Hodnota = skore dlazdice + strategicky postup k pristimu square, ktery by
    dlazdice sama prinesla. Skore postup nezna: dlazdice chybejici ve square
    okne ma skore jako kazda jina letos nenavstivena (~33), pritom jeji
    doplneni ma cenu stovek bodu (rucni trasa Stodulky -> Barrandov: 1 z 5
    dlazdic rocniho okna 12x12 = postup 235). Bez toho ji hledani nemelo proc
    zkusit - a strop MAX_CANDIDATES se proto uplatnuje az po serazeni."""
    from scoring import square_progress

    within = []
    for cand in candidates:
        tile = tuple(cand["tile"])
        lat, lon = tile_center(tile)
        if not _reachable(lat, lon, start, end, max_m):
            continue
        progress = square_progress({tile}, context) if context else 0.0
        within.append({"tile": tile, "score": cand["score"], "value": cand["score"] + progress,
                       "lat": lat, "lon": lon})
    within.sort(key=lambda item: -item["value"])
    return within[:MAX_CANDIDATES]


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


def _region_seeds(within, start, end, limit_m):
    """Nejcennejsi kandidat z kazde oblasti REGION_TILES x REGION_TILES - kazda
    oblast je jiny smer behu. Posloupnost se od nej pak doplni (_ratio_fill)
    tim, co je po ruce.

    Jen z kandidatu, na ktere samotna zajizdka nepretahne limit_m: nejcennejsi
    dlazdice oblasti byva ta nejvzdalenejsi (na jihu (8846, 5557), 332 bodu) a
    trasa k ni presahla okno, takze z ni po oprave zbyla prima cesta bez
    prinosu - zatimco o dlazdici vedle se stejnou hodnotou postupu (268) vedla
    rucni trasa uzivatele."""
    best = {}
    for cand in within:
        if _estimate_path_m(start, end, [cand]) > limit_m:
            continue
        cell = (cand["tile"][0] // REGION_TILES, cand["tile"][1] // REGION_TILES)
        if cell not in best or cand["value"] > best[cell]["value"]:
            best[cell] = cand
    ranked = sorted(best.values(), key=lambda cand: -cand["value"])
    return [[cand] for cand in ranked[:MAX_REGION_SEEDS]]


def _square_completion_seeds(within, context, start, end, max_m):
    """Seedy cilene na zvetseni max square: okna (side+1)^2 s nejvyse
    MAX_SQUARE_MISSING chybejicimi tiles, vsechny v dosahu. Jednotlive chybejici
    tiles maji samy o sobe nulovy square prinos (nesctitavost), takze by je
    obecne vyhledavani nemelo duvod kombinovat - proto dostavaji vlastni seed.
    Chybejici tile nemusi byt kandidat ze scoringu (score 0)."""
    from scoring import PERIODS, PRIORITY_WEIGHTS

    by_tile = {cand["tile"]: cand for cand in within}
    seeds = []
    seen = set()

    for period in PERIODS:
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

            # Chybejici tiles maji cenu jen SPOLECNE - kazdy nese svuj podil
            # hodnoty okna, jinak by je oprava pretecene trasy zahodila jako
            # prvni (samostatne maji skore nizke nebo zadne).
            value = weight * (2 * side - 1)
            share = value / len(missing)
            waypoints = []
            for tile in missing:
                if tile in by_tile:
                    cand = by_tile[tile]
                    waypoints.append({**cand, "value": max(cand["value"], share)})
                    continue
                lat, lon = tile_center(tile)
                if not _reachable(lat, lon, start, end, max_m):
                    waypoints = None
                    break
                waypoints.append({"tile": tile, "score": 0.0, "value": share, "lat": lat, "lon": lon})
            if waypoints:
                seeds.append((value, len(missing), waypoints))

    seeds.sort(key=lambda item: (-item[0], item[1]))
    return [waypoints for _, _, waypoints in seeds[:MAX_SQUARE_SEEDS]]


def _ratio_fill(start, end, sequence, pool, budget_m):
    """Doplnuje kandidaty podle hodnoty na pridany kilometr (odhad), dokud se
    odhad delky vejde do budget_m. Kazdy se vklada na nejlevnejsi misto.

    Pomer misto poradi skore: posloupnost pak roste tam, kde uz vede, a
    nemicha smery. Pridana delka se pocita aspon MIN_INSERT_M - dlazdice, kterou
    trasa protne skoro zadarmo, jinak ma nekonecny pomer bez ohledu na hodnotu."""
    sequence = list(sequence)
    points = _estimate_points(start, end, sequence)
    length = _estimate_path_m(start, end, sequence)
    used = {item["tile"] for item in sequence}

    while len(sequence) < MAX_WAYPOINTS:
        overhead = WAYPOINT_OVERHEAD_M if sequence else 0.0
        best = None
        for cand in pool:
            if cand["tile"] in used or cand["value"] <= 0:
                continue
            point = _estimate_point(cand)
            for position in range(len(points) - 1):
                before, after = points[position], points[position + 1]
                added = (_estimate_gap_m(before, point) + _estimate_gap_m(point, after)
                         - _estimate_gap_m(before, after) + overhead)
                if length + added > budget_m:
                    continue
                ratio = cand["value"] / max(added, MIN_INSERT_M)
                if best is None or ratio > best[0]:
                    best = (ratio, position, added, cand, point)
        if best is None:
            break
        _ratio, position, added, cand, point = best
        sequence.insert(position, cand)
        points.insert(position + 1, point)
        used.add(cand["tile"])
        length += added
    return sequence


def _rank_fill(start, end, pool, budget_m):
    """Doplnuje kandidaty v poradi HODNOTY (kazdy na nejlevnejsi misto), dokud se
    odhad vejde do budget_m. Doplnek k _ratio_fill: ten dava prednost kompaktnim
    skupinam blizkych dlazdic, tenhle jde i za vzdalenejsi cennou dlazdici.
    Jedna posloupnost navic v portfoliu - mereno: okruh 12 km ze Stodulek
    skore 247 -> 270, okruh 10 km z Karlova nam. 1,4 -> 1,5."""
    sequence = []
    for cand in sorted(pool, key=lambda item: -item["value"]):
        if len(sequence) >= MAX_WAYPOINTS:
            break
        if cand["value"] <= 0:
            continue
        trial = _ratio_fill(start, end, sequence, [cand], budget_m)
        if len(trial) > len(sequence):
            sequence = trial
    return sequence


def _drop_order(start, end, sequence):
    """Waypointy od nejmene cennych: hodnota na kilometr, ktery vypusteni usetri.

    Drive se vypoustel waypoint s nejnizsim skore dlazdice. Skore ale nerika,
    kolik ktera dlazdice stoji delky - a pri shode (102,38 vs 102,39) rozhodl
    bonus za stari: na trase Stodulky -> Barrandov tak vypadla cela jizni skupina
    (sama o sobe 606 bodu) a zustal sever (292)."""
    total = _estimate_path_m(start, end, sequence)

    def cost(item):
        rest = [other for other in sequence if other is not item]
        saved = total - _estimate_path_m(start, end, rest)
        return item["value"] / max(saved, MIN_INSERT_M)

    return sorted(sequence, key=cost)


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
                fillers.append({"tile": tile, "score": 0.0, "value": 0.0, "lat": lat, "lon": lon})
    return fillers


def _best_additions(start, end, sequence, pool, limit_m):
    """Posloupnosti o jeden waypoint delsi, od nejlepsiho pomeru hodnota /
    pridany kilometr (odhad)."""
    if len(sequence) >= MAX_WAYPOINTS:
        return []
    used = {item["tile"] for item in sequence}
    current = _estimate_path_m(start, end, sequence)
    scored = []
    for cand in pool:
        if cand["tile"] in used or cand["value"] <= 0:
            continue
        trial = _ratio_fill(start, end, sequence, [cand], limit_m)
        if len(trial) > len(sequence):
            added = _estimate_path_m(start, end, trial) - current
            scored.append((cand["value"] / max(added, MIN_INSERT_M), trial))
    scored.sort(key=lambda item: -item[0])
    return [trial for _ratio, trial in scored[:LOCAL_SEARCH_MOVES]]


def _neighbours(start, end, sequence, pool, limit_m, is_loop):
    """Sousedni posloupnosti pro lokalni hledani, nejslibnejsi tahy napred
    a druhy tahu prostridane, aby strop prepoctu nevycerpal jediny druh."""
    moves = []
    if is_loop and len(sequence) > 1:
        moves.append([list(reversed(sequence))])
    if len(sequence) > 1:
        order = _drop_order(start, end, sequence)
        moves.append([[item for item in sequence if item is not dropped]
                      for dropped in order[:LOCAL_SEARCH_MOVES]])
        weakest = order[0]
        rest = [item for item in sequence if item is not weakest]
        others = [cand for cand in pool if cand["tile"] != weakest["tile"]]
        moves.append(_best_additions(start, end, rest, others, limit_m))
    moves.append(_best_additions(start, end, sequence, pool, limit_m))

    trials = []
    for rank in range(max((len(kind) for kind in moves), default=0)):
        trials.extend(kind[rank] for kind in moves if rank < len(kind))
    return trials


def _local_search(details_for, key, best, pool, start, end, limit_m, is_loop):
    """Zlepsuje vitezze tahy _neighbours - prvni zlepseni se prijme a hleda
    se dal od nej, dokud nejaky tah pomaha a nevycerpa se LOCAL_SEARCH_EVALS."""
    tried = {tuple(item["tile"] for item in best["sequence"])}
    evaluations = 0
    improved = True
    while improved and evaluations < LOCAL_SEARCH_EVALS:
        improved = False
        for trial in _neighbours(start, end, best["sequence"], pool, limit_m, is_loop):
            signature = tuple(item["tile"] for item in trial)
            if signature in tried:
                continue
            if evaluations >= LOCAL_SEARCH_EVALS:
                break
            tried.add(signature)
            evaluations += 1
            details = details_for(trial)
            if details and key(details) > key(best):
                best = details
                improved = True
                break
    return best


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


def _shrink_toward_target(details_for, key, best, target_m, start, end):
    """Zkrati trasu k cilove delce vypustenim waypointu - dokud se skore zlepsuje.

    Zrcadlovy protejsek `_extend_to_window`. Pracuje se SKUTECNOU delkou, ne s
    odhadem: odhad (`_estimate_path_m`) systematicky podstreluje, takze i
    sekvence naplnena "jen po cil" vyjde po exaktnim prepoctu nad cilem. Delku
    proto nejde uridit pri stavbe sekvence, jen zpetnou vazbou z prepoctu.

    Zkousi se vypustit nekolik nejmene cennych waypointu (_drop_order: hodnota
    na usetreny kilometr podle odhadu) a vezme se nejlepsi exaktni vysledek -
    spolehnout se na jediny kandidat nestaci, protoze delku trasy
    urcuje poloha dlazdic, ne jejich pocet (mereno: vypusteni nejslabsiho
    waypointu trasu o 90 m PRODLOUZILO). Jestli se zkraceni vyplati, rozhoduje
    cilova funkce: kratsi trasa ma mensi prinos, ale i mensi odchylku delky.
    """
    current = best
    for _ in range(MAX_SHRINK_ROUNDS):
        sequence = current["sequence"]
        if len(sequence) <= 1 or current["length_m"] <= target_m:
            break

        weakest = _drop_order(start, end, sequence)[:SHRINK_CANDIDATES]
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
    Pri prekroceni max_m odpada waypoint s nejmensi hodnotou na usetreny
    kilometr (_drop_order). avoid_reuse penalizuje
    opakovany pruchod stejnou ulici, quiet_factor cesty podel vyznamnych ulic
    a trail_factor zvyhodnuje znacene trasy."""
    from scoring import evaluate_tile_set, square_progress

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
        weakest = _drop_order(start_point, finish, sequence)[0]
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
        "progress": square_progress(crossed, context),
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

    # Prinos + strategicky postup: dlazdice, ktera max square jeste nezvetsi, ale
    # priblizi ho, ma cenu - prave kvuli tomu se nekdy bezi (viz scoring.
    # square_progress). Merky kvality snizuji obojí stejne.
    return ((details["benefit"]["total"] + details.get("progress", 0.0))
            * (1 - CORRIDOR_PENALTY_FRACTION * corridor)
            * (1 - quiet_weight * major)
            * (1 - quiet_weight * TRAIL_PENALTY_FRACTION * off_trail)
            * (1 - LENGTH_PENALTY_FRACTION * deviation))


def plan_tile_loop(graph, start_lat, start_lon, target_km, tolerance_km, candidates, context,
                   end_lat=None, end_lon=None, quiet_weight=None):
    """Beh v delce target +- tolerance s nejvetsim spolecnym prinosem.

    Okruh (end == start, vychozi), nebo z bodu do bodu (end_lat/end_lon).
    Porovnava varianty: posloupnosti z ruznych oblasti (_region_seeds) a seedy
    na dokompletovani square, plnene podle hodnoty na kilometr k cilove delce
    (_ratio_fill + kalibrace odhadu), kazdou exaktne prepocita a ohodnoti
    spolecnym prinosem VSECH protnutych tiles (evaluate_tile_set - zisky mnoziny,
    ne soucet skore) snizenym o merky kvality (_variant_score). Vitez se jeste
    doladi lokalnim hledanim (_local_search: pridat, vypustit, vymenit, obratit).

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

    within = _within_reach(candidates, start, end, max_m, context)
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

    # Pomer skutecne/odhadnute delky z exaktnich prepoctu TETO ulohy (viz
    # INITIAL_CALIBRATION). Median, aby jedna podivna varianta neujela.
    ratios = []

    def calibration():
        if not ratios:
            return INITIAL_CALIBRATION
        return sorted(ratios)[len(ratios) // 2]

    def observe(details):
        estimate = _estimate_path_m(start, end, details["sequence"])
        if not details["sequence"] or estimate <= 0:
            return None
        low, high = CALIBRATION_BOUNDS
        ratios.append(min(max(details["length_m"] / estimate, low), high))
        return ratios[-1]

    def build(seed):
        """Posloupnost ze seedu naplnena k CILOVE delce. Kdyz exaktni delka
        vyjde od cile daleko, postavi se jeste jednou s pomerem zmerenym prave
        na ni - teren (a tim chyba odhadu) se lisi smer od smeru."""
        details = add_variant(_ratio_fill(start, end, seed, within, target_m / calibration()))
        if not details:
            return
        own = observe(details)
        if own and abs(details["length_m"] - target_m) > tolerance_m / 3:
            refill = add_variant(_ratio_fill(start, end, seed, within, target_m / own))
            if refill:
                observe(refill)

    build([])
    ranked = add_variant(_rank_fill(start, end, within, target_m / calibration()))
    if ranked:
        observe(ranked)
    for seed in _region_seeds(within, start, end, target_m / calibration()):
        build(seed)
    for square_seed in _square_completion_seeds(within, context, start, end, max_m):
        # Okno ma smysl jen cele - vsechny chybejici tiles napred, pak doplnit.
        seed = _ratio_fill(start, end, [], square_seed, math.inf)
        if len(seed) < len(square_seed):
            continue
        if _estimate_path_m(start, end, seed) * calibration() > max_m:
            continue
        build(seed)

    if not variants:
        raise RuntimeError("No walkable route found from the start point")

    # Hledani (lokalni hledani, prodlouzeni, zkraceni, vyber posloupnosti pro
    # nizkoopakovaci a klidne prepocty) bezi s PEVNYMI vahami klidu
    # (SEARCH_QUIET_WEIGHTS); vse, co najde, jde do portfolia a posuvnik az na
    # konci vybira.
    def perspective_key(weight):
        def key(details):
            return (details["in_window"],
                    _variant_score(details, target_m, tolerance_m, weight),
                    -details["length_m"])
        return key

    search_key = perspective_key(SEARCH_QUIET_WEIGHTS[0])

    def keep(details):
        if all(other is not details for other in variants):
            variants.append(details)
        return details

    perspective_best = []
    for weight in SEARCH_QUIET_WEIGHTS:
        key = perspective_key(weight)
        best = keep(_local_search(details_for, key, max(variants, key=key), within,
                                  start, end, max_m / calibration(), is_loop))

        # Kratsi trasa nez zadane okno: dotahni delku pres dalsi tiles v dosahu.
        if not best["in_window"] and best["length_m"] < min_m:
            best = keep(_extend_to_window(details_for, start, end, best, min_m, max_m))

        # Delsi nez cil: zkus ji zkratit k cilove delce. Rozhodne cilova funkce -
        # kratsi trasa ma mensi prinos, ale i mensi odchylku delky.
        if best["length_m"] > target_m:
            best = keep(_shrink_toward_target(details_for, key, best, target_m, start, end))
        perspective_best.append(best)

    # Nizkoopakovaci varianty patri do portfolia, ne az do finalizace vitezze:
    # nejlepsi posloupnosti se prepocitaji i s vyhybanim opakovanym ulicim a
    # souteri rovnocenne. (Jinak vyhraje trasa, ktera prinos nasbirala prave
    # opakovanim, a jeji "opravena" verze uz se neprosadi.)
    for details in sorted(variants, key=search_key, reverse=True)[:AVOID_VARIANTS]:
        # Vyhybani je drahe (hledani bez cache) - ma smysl jen tam, kde je co
        # zlepsovat; varianty s minimalnim opakovanim se preskakuji.
        if details["repeated_m"] <= AVOID_MIN_RATIO * details["length_m"]:
            continue
        add_variant(details["sequence"], avoid_reuse=True)

    # Klidne varianty stejnym vzorcem: tytez cilove dlazdice, ale usek se hleda
    # s prirazkou cestam podel vyznamnych ulic. Az takova varianta ukaze, kolik
    # klid opravdu stoji - odhadnout to z jedne trasy nejde. Pocitaji se i pri
    # nulove vaze, aby portfolio na posuvniku nezaviselo (viz QUIET_LEG_PROFILES).
    # Zdroje klidnych prepoctu z RUZNYCH smeru (_distinct_variants), ne prvnich
    # nekolik podle skore - ty vedou vetsinou tymz mistem a jejich klidne
    # prepocty jsou si podobne, takze posuvnik nemel z ceho vybirat.
    quiet_sources = _distinct_variants(variants, search_key, limit=QUIET_VARIANTS)
    quiet_sources += [details for details in perspective_best
                      if all(details is not other for other in quiet_sources)]
    for details in quiet_sources:
        for quiet_factor, trail_factor in QUIET_LEG_PROFILES:
            profile = {"avoid_reuse": True, "quiet_factor": quiet_factor, "trail_factor": trail_factor}
            quiet = add_variant(details["sequence"], **profile)
            # Klidna cesta je delsi. Posloupnost naplnena k cili pak cil
            # prestreli a delkova penalizace klidnou variantu vyradi - posuvnik
            # by nemel z ceho vybirat. Proto i verze o waypoint kratsi.
            if (quiet and len(quiet["sequence"]) > 1
                    and quiet["length_m"] > target_m + tolerance_m / 3):
                dropped = _drop_order(start, end, quiet["sequence"])[0]
                add_variant([item for item in quiet["sequence"] if item is not dropped], **profile)

    best = max(variants, key=variant_key)

    # Dlazdice, kvuli kterym se beh dela: ty, na ktere trasa mirila (waypointy),
    # plus vsechny doporucene, ktere cestou protne. Itinerar podle nich rekne,
    # kde a jak hluboko se sbira. Kandidati "jen stari" doporucenim nejsou -
    # byla by to kazda protnuta dlazdice; pocitaji se jen jako waypointy.
    recommended = {tuple(cand["tile"]) for cand in candidates if not cand.get("stale_only")}

    def output(details):
        length_m = details["length_m"] or 1.0
        waypoints = [item["tile"] for item in details["sequence"]]
        collected = [tile for tile in details["tiles_crossed"]
                     if tile in recommended or tile in waypoints]
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
            "progress": details["progress"],
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
    from scoring import PERIODS, build_route_context, find_tile_opportunities

    tile_dbs = {key: get_period_tile_database(key) for key in PERIODS}
    opportunities = find_tile_opportunities(tile_dbs)
    context = build_route_context(tile_dbs)

    reach_km = (args.distance + args.tolerance) / 2 + 0.5
    print(f"Loading walk graph around {args.lat:.4f}, {args.lon:.4f} (reach {reach_km:.1f} km)...")
    graph = load_walk_graph(args.lat, args.lon, reach_km)
    print(f"Graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges")

    route = plan_tile_loop(graph, args.lat, args.lon, args.distance, args.tolerance, opportunities, context)

    candidate_tiles = {tuple(o["tile"]) for o in opportunities if not o["stale_only"]}
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
