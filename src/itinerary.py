"""Itinerar behu - tahak na trasu.

Useky se stejnym popisem se slouci do kroku; ke kazdemu se uvadi KUMULATIVNI
vzdalenost od startu (tak si uzivatel itinerare pise rucne), smer zatoceni,
znacena trasa a orientacni body (co a kde trasa krizi).

Jedna kilometraz pro vsechno: vzdalenost ke krokum i k orientacnim bodum se
pocita ze skutecne delky hran, stejne jako delka trasy. Drive se orientacni body
meraly vzdusnou carou mezi uzly - obe stupnice se na referencnim okruhu rozesly
o 340 m a krizeni se hlasila jeste pred zacatkem sveho useku.
"""
import math
from collections import defaultdict

from geo import bearing, compass, haversine_m, tag
from geojson import lon_lat_tile, tile_lon_lat
from runcost import best_edge

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
FOOTLIKE_HIGHWAYS = {"footway", "steps", "pedestrian"}

# Prahy slucovani kroku itinerare: pojmenovany usek nese informaci, takze
# prezije i kratsi; bezejmenne prebehy se slucuji drive.
STEP_MIN_M = 250.0
STEP_MIN_NAMED_M = 100.0

# Krizovane orientacni body: vyznamna ulice blizko uzlu trasy, kolma na smer
# behu = "krizis X". (Vodni toky a zeleznice doplnuje az landmarks.py - v pesim
# grafu nejsou.)
LANDMARK_MAX_M = 35.0
LANDMARK_CROSS_MIN_DEG = 45.0  # min odchylka smeru, aby slo o krizeni, ne soubeh
# Tataz ulice u dvou sousednich uzlu je jeden prechod, ne dva orientacni body
# (siroka krizovatka ma uzlu vic). Deduplikuje se pres CELY itinerar - hranice
# kroku neni duvod hlasit Legerovu dvakrat po sobe. Stejny prah jako u bariér
# v landmarks.py.
CROSSING_DEDUP_M = 400.0

# Rozcesti na nepojmenovanych cestach: tam hrozi navigacni chyba, proto se
# hlasi kazde (na ulicich by to byl sum - tam staci nazev).
FORK_MIN_DEG = 25.0
FORK_IGNORED_HIGHWAYS = {"service", "steps", "corridor", "elevator"}
FORK_PATH_HIGHWAYS = {"track", "path", "bridleway", "footway", "cycleway"}
FORK_REPORT_KINDS = {"polni cesta", "pesina"}  # kde rozcesti hlasit


def _tile_depth_m(lat, lon, tile):
    """Jak hluboko od hranice dlazdice bod lezi. Rozhoduje o tom, jestli se
    navsteva zapocita i pri chybe GPS - trasa jen tecna k hranici je riziko."""
    x, y = tile
    west, north = tile_lon_lat(x, y)
    east, south = tile_lon_lat(x + 1, y + 1)
    coslat = math.cos(math.radians(lat))
    return min(
        (lat - south) * 111320.0,
        (north - lat) * 111320.0,
        (lon - west) * 111320.0 * coslat,
        (east - lon) * 111320.0 * coslat,
    )


def _tile_pickups(coordinates, targets, waypoints):
    """Useky, kde trasa sbira cilovou dlazdici - kvuli cemu se cely beh dela.

    Meri se po SOURADNICICH trasy, ne po uzlech grafu. Uzly jsou rozestoupene
    desitky metru, takze vjezd do dlazdice vychazel pozde a delka uvnitr kratsi,
    nez je (mereno: hlaseno "od 7,29 km, 0,22 km uvnitr", ve skutecnosti od
    7,24 km a 0,27 km). Souradnice navic kopiruji geometrii hran - tedy tytez
    body, ze kterych se pocita `tiles_crossed` a z nej prinos trasy, takze si
    obojí nemuze odporovat.

    Pocita se pres celou trasu najednou (ne po krocich), aby dlazdice pretekajici
    pres nekolik useku dala jeden zaznam se skutecnou hloubkou pruniku.
    """
    runs = []
    current = None
    travelled = 0.0
    previous = None

    for lat, lon in coordinates:
        if previous is not None:
            travelled += haversine_m(previous[0], previous[1], lat, lon)
        previous = (lat, lon)

        tile = lon_lat_tile(lon, lat)
        if tile not in targets:
            current = None
            continue
        depth = _tile_depth_m(lat, lon, tile)
        if current is not None and current["tile"] == tile:
            current["end_m"] = travelled
            current["depth"] = max(current["depth"], depth)
        else:
            current = {"tile": tile, "start_m": travelled,
                       "end_m": travelled, "depth": depth}
            runs.append(current)

    return [
        {
            "at_m": run["start_m"],
            "tile": list(run["tile"]),
            "at_km": round(run["start_m"] / 1000, 2),
            "km": round((run["end_m"] - run["start_m"]) / 1000, 2),
            "depth_m": round(run["depth"]),
            "waypoint": run["tile"] in waypoints,
        }
        for run in runs
    ]


def _turn_word(previous_bearing, next_bearing):
    if previous_bearing is None:
        return None
    delta = (next_bearing - previous_bearing + 540) % 360 - 180
    if abs(delta) < 35:
        return None
    if abs(delta) > 150:
        return "zpet"
    return "vpravo" if delta > 0 else "vlevo"


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
    return bearing(graph, anchor, end) if from_start else bearing(graph, end, anchor)


def _travel_bearing(graph, node_path, index):
    """Smer, kterym trasa prochazi uzlem (tetiva pres nej). Podle nej se pozna,
    jestli je blizka ulice krizena, nebo jen soubezna - smer proto MUSI vychazet
    z trasy. Libovolny soused uzlu v grafu (treba bocni ulice, kterou trasa
    nepouzije) hlasil soubezne ulice jako krizene."""
    previous, node, following = node_path[index - 1], node_path[index], node_path[index + 1]
    if previous == following:  # slepa odbocka tam a zpet
        return bearing(graph, previous, node)
    return bearing(graph, previous, following)


def _crossed_street(graph, node, travel_bearing):
    """Nazev vyznamne ulice, kterou trasa v danem uzlu krizi (je blizko a kolma
    ke smeru behu) - orientacni bod pro tahak. Pojmenovane ulice pochazeji z
    indexu ulozeneho na grafu (waygraph.enrich_streets)."""
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

    own = travel_bearing % 180
    diff = np.abs(bearings[close] - own)
    diff = np.minimum(diff, 180 - diff)
    crossing = close[diff >= LANDMARK_CROSS_MIN_DEG]
    if not len(crossing):
        return None
    return str(names[int(crossing[np.argmin(meters[crossing])])])


def _fork_at(graph, node, previous_node, next_node):
    """Rozcesti: existuje-li z uzlu jina schudna cesta nez ta, kterou trasa
    pokracuje, vrati, kterym smerem se drzet ('vlevo'/'vpravo'/'rovne')."""
    route_bearing = bearing(graph, node, next_node)
    deltas = []
    for neighbour in set(graph.successors(node)) | set(graph.predecessors(node)):
        if neighbour in (previous_node, next_node):
            continue
        edge = best_edge(graph, node, neighbour) if graph.has_edge(node, neighbour) else None
        if edge is None:
            edge = best_edge(graph, neighbour, node)
        highway = tag(edge, "highway")
        if highway in FORK_IGNORED_HIGHWAYS or highway not in FORK_PATH_HIGHWAYS:
            continue
        delta = (bearing(graph, node, neighbour) - route_bearing + 540) % 360 - 180
        if abs(delta) >= FORK_MIN_DEG:
            deltas.append(delta)

    if not deltas:
        return None
    # kdyz odbocka vede vpravo, trasa pokracuje vlevo (a naopak)
    if all(delta > 0 for delta in deltas):
        return "vlevo"
    if all(delta < 0 for delta in deltas):
        return "vpravo"
    return "rovne"


def _segment_name(graph, u, v):
    """(nazev useku nebo None, typ cesty) - jen popis samotneho useku.
    Chodniky bez jmena maji ulici doplnenou v atributu along_street
    (waygraph.enrich_streets)."""
    edge = best_edge(graph, u, v)
    highway = tag(edge, "highway")
    kind = WAY_LABELS.get(highway, "cesta")

    name = tag(edge, "name")
    if name:
        return name, kind
    along = edge.get("along_street")
    if along:
        # u chodniku staci nazev ulice, beh po chodniku je vychozi predpoklad
        return (along if highway in FOOTLIKE_HIGHWAYS else f"{kind} podel {along}"), kind
    ref = tag(edge, "ref")
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


def _vote_trail(step):
    """Znacena trasa kroku, kdyz po ni vede aspon polovina jeho delky."""
    votes = defaultdict(float)
    for trail, length in step["trails"]:
        if trail:
            votes[trail] += length
    if not votes:
        return None
    best, covered = max(votes.items(), key=lambda item: item[1])
    return best if covered >= 0.3 * step["m"] else None


def route_directions(graph, node_path, target_tiles=(), waypoint_tiles=(), coordinates=None):
    """Itinerar behu: useky se stejnym popisem slouceny, s kumulativni
    vzdalenosti, smerem zatoceni a orientacnimi body (krizene vyznamne ulice).
    Slouzi jako tahak na trasu.

    target_tiles/waypoint_tiles - dlazdice, kvuli kterym se beh dela. Bez nich
    itinerar mlci o ucelu cele trasy: rekne, kudy bezet, ale ne kde a jak hluboko
    se sbira."""
    if len(node_path) < 2:
        return []

    targets = {tuple(tile) for tile in target_tiles}
    waypoints = {tuple(tile) for tile in waypoint_tiles}
    targets |= waypoints

    # Kumulativni vzdalenost k jednotlivym bodum trasy ze SKUTECNE delky hran -
    # stejne jako delka trasy i delka kroku (viz docstring modulu).
    cumulative = [0.0]
    for u, v in zip(node_path, node_path[1:]):
        cumulative.append(cumulative[-1] + float(best_edge(graph, u, v)["length"]))

    # Kroky si drzi INDEXY do node_path, ne uzly: uzel se muze na trase
    # opakovat, index urcuje misto jednoznacne (a rovnou i jeho kilometraz).
    steps = []
    for index, (u, v) in enumerate(zip(node_path, node_path[1:])):
        edge = best_edge(graph, u, v)
        name, kind = _segment_name(graph, u, v)
        label = name or kind
        length = float(edge["length"])
        bridge = bool(tag(edge, "bridge")) and tag(edge, "bridge") != "no"
        steps_here = tag(edge, "highway") == "steps"

        trail = edge.get("trail")
        if steps and steps[-1]["label"] == label:
            steps[-1]["m"] += length
            steps[-1]["bridge"] = steps[-1]["bridge"] or bridge
            steps[-1]["steps"] = steps[-1]["steps"] or steps_here
            steps[-1]["names"].append((name, length))
            steps[-1]["trails"].append((trail, length))
            steps[-1]["nodes"].append(index + 1)
        else:
            steps.append({
                "label": label, "kind": kind, "named": bool(name), "m": length,
                "bridge": bridge, "steps": steps_here,
                "names": [(name, length)], "trails": [(trail, length)],
                "nodes": [index, index + 1],
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
        keeper["trails"] += gone["trails"]
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

    if coordinates is None:
        from waygraph import path_coordinates
        coordinates = path_coordinates(graph, node_path)
    pickups = _tile_pickups(coordinates, targets, waypoints)
    pickup_index = 0  # kazdy sber patri prave jednomu kroku

    labels = [step["label"] for step in merged]
    directions = []
    previous_out = None
    seen_crossings = {}  # nazev ulice -> kde naposledy hlasena (pres cely itinerar)

    for index, step in enumerate(merged):
        step_nodes = [node_path[position] for position in step["nodes"]]
        bearing_in = _smoothed_bearing(graph, step_nodes, from_start=True)
        start_m = cumulative[step["nodes"][0]]

        # krizeni s km; vynech ulici, po ktere prave bezime nebo hned pobezime
        neighbour_labels = {step["label"]}
        if index > 0:
            neighbour_labels.add(labels[index - 1])
        if index + 1 < len(labels):
            neighbour_labels.add(labels[index + 1])

        crossings = []
        for position in step["nodes"][1:-1]:
            crossed = _crossed_street(
                graph, node_path[position], _travel_bearing(graph, node_path, position)
            )
            if not crossed or crossed in neighbour_labels:
                continue
            at_m = cumulative[position]
            if at_m - seen_crossings.get(crossed, -math.inf) < CROSSING_DEDUP_M:
                continue
            seen_crossings[crossed] = at_m
            crossings.append({"name": crossed, "at_km": round(at_m / 1000, 2)})

        # Zmena nazvu ulice bez zatoceni neni pokyn - drive se hlasila jako
        # "rovne" a delala pulku itinerare.
        turn = _turn_word(previous_out, bearing_in)

        # Rozcesti hlasime jen na NEZNACENYCH polnich cestach a pesinach: tam
        # hrozi navigacni chyba. Na chodnicich v zastavbe je kazdy vjezd
        # rozcesti (desitky na kilometr) a znacena trasa se poznat da podle
        # znacek.
        trail = _vote_trail(step)
        forks = []
        if step["kind"] in FORK_REPORT_KINDS and not trail:
            for position in step["nodes"][1:-1]:
                keep = _fork_at(graph, node_path[position],
                                node_path[position - 1], node_path[position + 1])
                if keep:
                    forks.append({"at_km": round(cumulative[position] / 1000, 2), "keep": keep})

        # Sber dlazdic patri ke kroku, ve kterem do ni trasa vjede. Prirazuje se
        # podle kilometraze a odebira po poradku, takze kazdy sber patri prave
        # jednomu kroku a sber presne na hranici pripadne tomu nasledujicimu.
        last_step = index == len(merged) - 1
        step_end_m = start_m + step["m"]
        tiles = []
        while pickup_index < len(pickups) and (
                last_step or pickups[pickup_index]["at_m"] < step_end_m):
            pickup = pickups[pickup_index]
            tiles.append({key: value for key, value in pickup.items() if key != "at_m"})
            pickup_index += 1

        directions.append({
            "at_km": round(start_m / 1000, 2),
            "km": round(step["m"] / 1000, 2),
            "tiles": tiles,
            "label": step["label"],
            "kind": step["kind"],
            "trail": trail,
            "turn": turn,
            "start_heading": compass(bearing_in) if index == 0 else None,
            "bridge": step["bridge"],
            "steps": step["steps"],
            "crossings": crossings,
            "forks": forks,
        })
        previous_out = _smoothed_bearing(graph, step_nodes, from_start=False)
    return directions
