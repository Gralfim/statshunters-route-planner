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
# Bezejmenny usek se pohlcuje az pod 50 m. Pri prahu 250 m se do sousedni
# POJMENOVANE ulice vlily i stovky metru chodniku a krok se pak jmenoval podle
# ulice, na kterou trasa teprve najede (mereno na vyprave do Zbuzan: 181 m
# chodniku pred Plzenskou, 85 m polni cesty pred Mladkovou). Namerene delky
# bezejmennych useku na te trase maji cistou mezeru: spojky konci na 43 m,
# skutecne useky zacinaji na 70 m.
STEP_MIN_M = 50.0
# Pojmenovany usek se pohlcuje az pod 30 m. Pri prahu 100 m mizely z popisu cele
# ulice, po kterych se doopravdy bezi: krok se pak jmenoval podle ulice, na
# kterou trasa teprve najede (mereno na vyprave do Zbuzan - "Mezi Lany" zacinalo
# 36 m po U Opatrovny, "Do Vrsku" dokonce 162 m po Oresske). Kdo se tim ridi,
# hleda odbocku, ktera tam jeste neni. Pod 30 m uz jde o sum: trasa zavadi na
# roh ulice na par metru (Puchmajerova 5 m, Walterovo namesti 7 m).
STEP_MIN_NAMED_M = 30.0
# Bezejmenny utrzek do teto delky mezi dvema kroky teze ulice se zaceli - je to
# prechod nebo spojka, ne samostatny usek (viz heal_gaps).
STEP_GAP_MAX_M = 25.0
# Znacka se u kroku uvadi od tohoto podilu delky; kdyz nepokryva skoro cely
# krok, pise se i usek, po kterem vede.
TRAIL_MIN_SHARE = 0.3
TRAIL_WHOLE_SHARE = 0.9
# Od tohoto podilu se navazujici kroky po teze turisticke znacce spoji do jednoho.
TRAIL_JOIN_SHARE = 0.5
# Znacka bez barvy neni v CR pouzitelne voditko: turisticke trasy jsou vzdy
# barevne znacene, jinak jde o naucnou stezku s vlastnim nazvem. Genericke
# popisky vznikaji z relaci bez osmc:symbol a bezce jen matou.
UNUSABLE_TRAILS = {"turisticka znacka", "cyklotrasa"}


def _is_hiking(trail):
    """Turisticka znacka (barevna nebo naucna stezka) - na rozdil od cyklotras
    je v CR znacena spolehlive, takze se da bezet podle ni."""
    return bool(trail) and (trail.endswith("turisticka") or trail.startswith("naucna"))

# Odstupnovani pokynu k zatoceni.
TURN_MIN_DEG = 35.0      # pod tim to neni zatacka, jen ohyb cesty
TURN_GENTLE_DEG = 60.0   # 35-60 = "mirne vlevo"
TURN_SHARP_DEG = 120.0   # 120-150 = "ostre vlevo"
TURN_BACK_DEG = 150.0    # nad tim "zpet"

# Rozhodovaci body uvnitr useku - kde se da zabloudit, i kdyz se nemeni nazev
# cesty. Slucovani kroku podle nazvu takova mista schovava: na referencni trase
# ma itinerar 40 kroku a jen 18 pokynu, pritom zatacek na krizovatkach je 134.
DECISION_WINDOW_M = 50.0    # smer se vyhlazuje (viz _decision_points)
DECISION_MIN_DEG = 50.0
DECISION_CLUSTER_M = 60.0
DECISION_SKIP_HIGHWAYS = {"service", "steps", "corridor", "elevator", "platform",
                          "construction"}

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
    """Pokyn k zatoceni, odstupnovany podle ostrosti. Bez odstupnovani se
    mirny ohyb 40 st a vlasenka 140 st cetly stejne."""
    if previous_bearing is None:
        return None
    delta = (next_bearing - previous_bearing + 540) % 360 - 180
    size = abs(delta)
    if size < TURN_MIN_DEG:
        return None
    if size > TURN_BACK_DEG:
        return "zpet"
    side = "vpravo" if delta > 0 else "vlevo"
    if size < TURN_GENTLE_DEG:
        return f"mirne {side}"
    if size >= TURN_SHARP_DEG:
        return f"ostre {side}"
    return side


def _path_bearing(graph, node_path, index, forward, window_m=DECISION_WINDOW_M):
    """Smer trasy pres ~window_m pred uzlem (forward=False) nebo za nim."""
    step = 1 if forward else -1
    travelled = 0.0
    position = index
    while 0 <= position + step < len(node_path) and travelled < window_m:
        travelled += haversine_m(
            graph.nodes[node_path[position]]["y"], graph.nodes[node_path[position]]["x"],
            graph.nodes[node_path[position + step]]["y"], graph.nodes[node_path[position + step]]["x"],
        )
        position += step
    if position == index:
        return None
    anchor = node_path[index]
    return (bearing(graph, anchor, node_path[position]) if forward
            else bearing(graph, node_path[position], anchor))


def _has_alternative(graph, node, previous_node, next_node):
    """Da se z uzlu pokracovat i jinam nez kudy trasa vede? Kde ne, tam neni co
    splest a pokyn je zbytecny."""
    for other in set(graph.successors(node)) | set(graph.predecessors(node)):
        if other in (previous_node, next_node):
            continue
        edge = (best_edge(graph, node, other) if graph.has_edge(node, other)
                else best_edge(graph, other, node))
        if tag(edge, "highway") not in DECISION_SKIP_HIGHWAYS:
            return True
    return False


def _decision_points(graph, node_path, cumulative):
    """Mista, kde se da zabloudit: vyrazna zmena smeru A moznost jit jinam.

    Dve veci to museji ustat, jinak jsou pokynu stovky (mereno na okruhu
    15 km z Karlova nam.):

    1. Smer se bere VYHLAZENY pres ~50 m. Jednotliva hrana v siti chodniku
       (prechod, obejiti rohu) jinak vypada jako zatacka - 134 bodu.
    2. Sousedni detekce se SHLUKUJI. Jedna zatacka se projevi na nekolika
       uzlech za sebou (uzly jsou po ~39 m), takze bez shluku jich zbyde 90.

    Po obojim + podmince, ze z uzlu vede jeste jina cesta, vyjde 24 pokynu
    (1,6 na km) - tolik uz tahak unese.
    """
    hits = []
    for index in range(1, len(node_path) - 1):
        before = _path_bearing(graph, node_path, index, forward=False)
        after = _path_bearing(graph, node_path, index, forward=True)
        if before is None or after is None:
            continue
        delta = abs((after - before + 540) % 360 - 180)
        if delta < DECISION_MIN_DEG:
            continue
        if not _has_alternative(graph, node_path[index],
                                node_path[index - 1], node_path[index + 1]):
            continue
        hits.append((index, delta, _turn_word(before, after)))

    decisions = []
    last_seen = None
    for index, delta, word in hits:
        if decisions and cumulative[index] - cumulative[last_seen] <= DECISION_CLUSTER_M:
            if delta > decisions[-1]["delta"]:  # ze shluku plati nejostrejsi
                decisions[-1].update(index=index, delta=delta, turn=word)
        else:
            decisions.append({"index": index, "delta": delta, "turn": word})
        last_seen = index
    return decisions


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


def _step_streets(step, min_share=0.05):
    """Ulice, kterymi krok vede, v poradi a bez drobtu. Slouzi jako poznamka
    tam, kde krok popisuje znacena trasa."""
    streets, seen = [], set()
    for name, length in step["names"]:
        if name and name not in seen and length >= min_share * step["m"]:
            seen.add(name)
            streets.append(name)
    return streets


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


def _vote_trail(step, cumulative):
    """Znacena trasa kroku a usek, po kterem po ni trasa opravdu vede.

    Znacka je pro bezce navigacni voditko: kdyz ji itinerar pripise celemu
    kroku, ale ona v polovine odbocí jinam, bezec ji poslechne a odbocí taky.
    Mereno na vyprave do Zbuzan - zelena turisticka pokryvala 295 m ze
    670metroveho useku (44 %) a itinerar ji uvadel pro cely usek.

    Vraci (nazev, od_m, do_m) nebo None. Kdyz znacka pokryva skoro cely krok,
    je rozsah None - nema smysl ho vypisovat.
    """
    votes = defaultdict(float)
    for trail, length in step["trails"]:
        if trail and trail not in UNUSABLE_TRAILS:
            votes[trail] += length
    if not votes:
        return None

    best, covered = max(votes.items(), key=lambda item: item[1])
    if covered < TRAIL_MIN_SHARE * step["m"]:
        return None
    if covered >= TRAIL_WHOLE_SHARE * step["m"]:
        return best, None, None

    marked = [index for index, (trail, _length) in enumerate(step["trails"]) if trail == best]
    return (best,
            cumulative[step["nodes"][marked[0]]],
            cumulative[step["nodes"][marked[-1] + 1]])


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

    # Rozhodovaci body se hledaji jeste pred slucovanim kroku - slucovani je
    # podle nich omezene (viz nize).
    decisions = _decision_points(graph, node_path, cumulative)
    decision_at = {item["index"]: item for item in decisions}

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

        own = float(length) if tag(edge, "name") else 0.0
        trail = edge.get("trail")
        if steps and steps[-1]["label"] == label:
            steps[-1]["m"] += length
            steps[-1]["bridge"] = steps[-1]["bridge"] or bridge
            steps[-1]["steps"] = steps[-1]["steps"] or steps_here
            steps[-1]["own_m"] += own
            steps[-1]["names"].append((name, length))
            steps[-1]["trails"].append((trail, length))
            steps[-1]["nodes"].append(index + 1)
        else:
            steps.append({
                "label": label, "kind": kind, "named": bool(name), "m": length, "own_m": own,
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
        # names/trails musi zustat zarovnane s nodes (i-ty zaznam = hrana z
        # nodes[i] do nodes[i+1]) - jinak nejde rict, kde po kroku vede znacka.
        keeper["m"] += gone["m"]
        keeper["own_m"] += gone["own_m"]
        keeper["bridge"] = keeper["bridge"] or gone["bridge"]
        keeper["steps"] = keeper["steps"] or gone["steps"]
        if append:
            keeper["names"] += gone["names"]
            keeper["trails"] += gone["trails"]
            keeper["nodes"] += gone["nodes"][1:]
        else:
            keeper["names"] = gone["names"] + keeper["names"]
            keeper["trails"] = gone["trails"] + keeper["trails"]
            keeper["nodes"] = gone["nodes"][:-1] + keeper["nodes"]

    def heal_gaps():
        """Zaceli kratky bezejmenny utrzek mezi dvema kroky teze ulice.

        Par metru bez nazvu (prechod, spojka) rozdeli ulici na dva kroky a kazdy
        z nich je pak "prilis kratky" - slucovani je rozebere do sousedu a ulice
        z popisu zmizi. Mereno: Oresska (163 m) rozseknuta 5metrovym utrzkem na
        81 + 77 m, oboji pod prahem 100 m; itinerar pak tvrdil "mirne vlevo Do
        Vrsku" uz 162 m pred tim, nez Do Vrsku vubec zacina.
        """
        index = 1
        while index < len(steps) - 1:
            middle, before, after = steps[index], steps[index - 1], steps[index + 1]
            if (not middle["named"] and middle["m"] <= STEP_GAP_MAX_M
                    and before["label"] == after["label"]):
                absorb(before, middle, append=True)
                absorb(before, after, append=True)
                del steps[index:index + 2]
            else:
                index += 1

    heal_gaps()

    def joinable(left):
        """Smi krok `left` splynout s nasledujicim? Ne, kdyz je na jejich
        rozhrani rozhodovaci bod.

        Bez teto podminky slucovani odbocku spolklo i s nazvem ulice, po ktere
        se pred ni bezi: hlaseni "mirne vlevo Do Vrsku" ve skutecnosti znamenalo
        "mirne vlevo na chodnik podel Oresske, pak vpravo Do Vrsku". Kdo se tim
        ridi, bud neodbocí vubec, nebo pokracuje po Oresske.
        """
        return steps[left]["nodes"][-1] not in decision_at

    while len(steps) > 1:
        candidates = []
        for index, step in enumerate(steps):
            if not too_short(step):
                continue
            targets_here = []
            if index > 0 and joinable(index - 1):
                targets_here.append(index - 1)
            if index + 1 < len(steps) and joinable(index):
                targets_here.append(index + 1)
            if targets_here:
                candidates.append((index, targets_here))
        if not candidates:
            break

        shortest, targets_here = min(candidates, key=lambda item: steps[item[0]]["m"])
        # popis prebira delsi soused - z tech, se kterymi se splynout smi
        target = max(targets_here, key=lambda index: steps[index]["m"])

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

    # Sousedni kroky, ktere nesou TUTEZ informaci, se spoji. Bez toho se jedna
    # ulice tristi jen tim, ze se meni charakter cesty ("pesina podel Novoveske"
    # + "Novoveska"), nebo se cesta na chvili priblizi nejake ulici a hned zase
    # vzdali ("pesina" + "pesina podel Jitrocelove" + "pesina").
    def street_of(step):
        """Ulice, ke ktere krok patri - vlastnim nazvem nebo soubeznosti."""
        votes = defaultdict(float)
        for name, length in step["names"]:
            if name:
                votes[name.split(" podel ", 1)[-1]] += length
        return max(votes, key=votes.get) if votes else None

    def owns_street(step):
        """Ma krok ulici jako VLASTNI nazev, nebo k ni jen prilehá? Chodnik,
        ktery se nahodou priblizi ulici, nese mnohem slabsi informaci."""
        return step["own_m"] >= 0.5 * step["m"]

    def same_information(left, right):
        left_street, right_street = street_of(left), street_of(right)
        if left_street and right_street:
            return left_street == right_street       # tataz ulice, jiny povrch
        if left["kind"] != right["kind"]:
            return False
        # jeden ma ulici, druhy ne: spojit jen tehdy, kdyz jde o pouhou
        # soubeznost - jinak by se bezejmenny usek prejmenoval po sousedovi
        other = left if left_street else right
        return not owns_street(other)

    joined = []
    for step in merged:
        if joined and same_information(joined[-1], step):
            keeper = joined[-1]
            # Popis prebira ta cast, ktera ulici opravdu NESE. Kdyz se k ni druha
            # jen prilehá ("pesina podel Jitrocelove"), zustava obecny popis
            # cesty - jinak by se z nahodne soubeznosti stal nazev celeho useku.
            takeover = owns_street(step) and not owns_street(keeper)
            absorb(keeper, step, append=True)
            if takeover:
                keeper["label"], keeper["named"] = step["label"], True
        else:
            joined.append(step)
    merged = joined

    # Turisticka znacka je v CR spolehlivejsi voditko nez nazvy ulic - kdo bezi
    # po zelene, sleduje znacky, ne cedule s nazvy ulic. Navazujici kroky po
    # teze znacce se proto spoji do jednoho a ulice zustanou jen v poznamce.
    def hiking_trail(step):
        """Znacka, po ktere krok prevazne vede. Nestaci pozadovat, aby ji
        pokryvala cela - parovani znacek ma toleranci 35 m a na dlouhem useku
        obcas vypadne, takze by se stretch po zlute nespojil."""
        marked = _vote_trail(step, cumulative)
        if not marked or not _is_hiking(marked[0]):
            return None
        covered = sum(length for trail, length in step["trails"] if trail == marked[0])
        return marked[0] if covered >= TRAIL_JOIN_SHARE * step["m"] else None

    joined = []
    for step in merged:
        trail = hiking_trail(step)
        if joined and trail and hiking_trail(joined[-1]) == trail:
            absorb(joined[-1], step, append=True)
            joined[-1]["label"], joined[-1]["named"] = trail, True
        else:
            joined.append(step)
    merged = joined

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

        # Rozhodovaci bod u zacatku useku je tataz zatacka jako prechod mezi
        # ulicemi - patri do pokynu kroku, ne mezi body uvnitr.
        step_end_m = start_m + step["m"]
        at_start = [
            item for item in decisions
            if item["turn"] and step["nodes"][0] <= item["index"] < step["nodes"][-1]
            and cumulative[item["index"]] - start_m <= DECISION_CLUSTER_M
        ]

        # Zmena nazvu ulice bez zatoceni neni pokyn - drive se hlasila jako
        # "rovne" a delala pulku itinerare. Kdyz ale na hranici useku
        # rozhodovaci bod je, pokyn se doplni: prechod na jinou cestu bez
        # vyrazneho zatoceni je prave to, co se snadno prejede.
        turn = _turn_word(previous_out, bearing_in)
        if turn is None and at_start:
            turn = at_start[0]["turn"]

        # Rozhodovaci body UVNITR useku - ty by jinak zanikly, protoze krok
        # vznika az zmenou nazvu cesty. Body u obou hranic se vynechavaji:
        # patri k pokynu tohoto nebo nasledujiciho kroku.
        inside = [
            {"at_km": round(cumulative[item["index"]] / 1000, 2), "turn": item["turn"]}
            for item in decisions
            if step["nodes"][0] < item["index"] < step["nodes"][-1] and item["turn"]
            and cumulative[item["index"]] - start_m > DECISION_CLUSTER_M
            and step_end_m - cumulative[item["index"]] > DECISION_CLUSTER_M
        ]

        # Rozcesti hlasime jen na NEZNACENYCH polnich cestach a pesinach: tam
        # hrozi navigacni chyba. Na chodnicich v zastavbe je kazdy vjezd
        # rozcesti (desitky na kilometr) a znacena trasa se poznat da podle
        # znacek.
        marked = _vote_trail(step, cumulative)
        trail, trail_from_m, trail_to_m = marked if marked else (None, None, None)
        # Kdyz znacka krok nepokryva cely, rozcesti se hlasi dal: od mista, kde
        # znacka odbocí, uz se podle ni orientovat nedá.
        forks = []
        if step["kind"] in FORK_REPORT_KINDS:
            # Tam, kde vede znacka, se bezec ridi podle ni; hlasi se proto jen
            # rozcesti ZA mistem, kde znacka odbocí pryc.
            from_m = start_m if trail is None else (trail_to_m if trail_from_m is not None else None)
            last_m = -math.inf
            for position in step["nodes"][1:-1]:
                at_m = cumulative[position]
                if from_m is None or at_m < from_m:
                    continue
                if at_m - last_m < DECISION_CLUSTER_M:
                    continue  # jedno rozcesti se projevi na nekolika uzlech
                keep = _fork_at(graph, node_path[position],
                                node_path[position - 1], node_path[position + 1])
                if keep:
                    last_m = at_m
                    forks.append({"at_km": round(at_m / 1000, 2), "keep": keep})

        # Sber dlazdic patri ke kroku, ve kterem do ni trasa vjede. Prirazuje se
        # podle kilometraze a odebira po poradku, takze kazdy sber patri prave
        # jednomu kroku a sber presne na hranici pripadne tomu nasledujicimu.
        last_step = index == len(merged) - 1
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
            # usek, po kterem znacka opravdu vede (None = po celem kroku)
            "trail_km": None if trail_from_m is None else
                        [round(trail_from_m / 1000, 2), round(trail_to_m / 1000, 2)],
            # ulice pod znackou - kdyz krok popisuje znacka, jmena zustanou tady
            "via": _step_streets(step) if step["label"] == trail else [],
            "turn": turn,
            "decisions": inside,
            "start_heading": compass(bearing_in) if index == 0 else None,
            "bridge": step["bridge"],
            "steps": step["steps"],
            "crossings": crossings,
            "forks": forks,
        })
        previous_out = _smoothed_bearing(graph, step_nodes, from_start=False)
    return directions
