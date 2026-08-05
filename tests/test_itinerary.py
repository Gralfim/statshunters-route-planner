"""Itinerar behu: kilometraz, orientacni body, pokyny k zataceni.

Graf je rucne postaveny primy usek na 50. rovnobezce, kde je delka hran zadana
NEZAVISLE na vzdalenosti uzlu (v OSM `length` kopiruje geometrii ulice, kdezto
uzly jsou jen krizovatky). Prave na tom rozdilu se poznaji chyby kilometraze.
"""
import math

import pytest

from geojson import lon_lat_tile, tile_lon_lat
from itinerary import _tile_depth_m, route_directions

LAT = 50.0
NODE_SPACING_M = 100.0
LON_PER_M = 1 / (111320.0 * math.cos(math.radians(LAT)))

# Uzly A..G v rade od zapadu na vychod, mezi sousedy vzdy NODE_SPACING_M.
CHAIN = ["A", "B", "C", "D", "E", "F", "G"]
# Delky hran (metry po ose ulice) - zamerne jine nez vzdalenost uzlu.
EDGE_LENGTHS = [400.0, 400.0, 150.0, 150.0, 150.0, 150.0]
EDGE_NAMES = ["Dlouha", "Dlouha", "Bocni", "Bocni", "Treti", "Treti"]
TOTAL_M = sum(EDGE_LENGTHS)

# Kumulativni vzdalenost k uzlum podle delek hran (to je ta spravna kilometraz).
AT_NODE_M = {"A": 0.0}
for _name, _length in zip(CHAIN[1:], EDGE_LENGTHS):
    AT_NODE_M[_name] = AT_NODE_M[CHAIN[CHAIN.index(_name) - 1]] + _length


def node_position(index):
    return (LAT, 14.4 + index * NODE_SPACING_M * LON_PER_M)


def segment_at(node_name, offset_m, bearing_deg, length_m=20.0):
    """Kratky usek ulice u uzlu: offset_m severne, natoceny bearing_deg."""
    lat, lon = node_position(CHAIN.index(node_name))
    lat += offset_m / 111320.0
    half = length_m / 2
    dlat = half * math.cos(math.radians(bearing_deg)) / 111320.0
    dlon = half * math.sin(math.radians(bearing_deg)) * LON_PER_M
    return [[lon - dlon, lat - dlat], [lon + dlon, lat + dlat]]


@pytest.fixture
def straight_route(line_graph):
    """Graf + trasa A..G. U uzlu B odbocuje bokem ulice, kterou trasa nepouzije
    (kvuli ni nelze smer behu odvozovat z libovolneho souseda v grafu)."""
    from landmarks import street_segments

    nodes = {name: node_position(index) for index, name in enumerate(CHAIN)}
    nodes["S"] = (LAT + 200 * (1 / 111320.0), node_position(1)[1])

    # Bocni ulice se vklada PRVNI, takze je prvni v successors(B).
    edges = [("B", "S", {"name": "Bocni odbocka", "length": 200.0})]
    for index, (length, name) in enumerate(zip(EDGE_LENGTHS, EDGE_NAMES)):
        edges.append((CHAIN[index], CHAIN[index + 1], {"name": name, "length": length}))

    graph = line_graph(nodes, edges)
    graph.graph["street_segments"] = street_segments([
        # soubezna s trasou u B - neni to krizeni, jen ulice vedle
        ["Soubezna", True, segment_at("B", 10, 90)],
        # kolme na trasu u D a znovu u F (300 m po sobe - druhe hlaseni je duplicita)
        ["Krizna", True, segment_at("D", 10, 0)],
        ["Krizna", True, segment_at("F", 10, 0)],
    ])
    return graph, list(CHAIN)


def test_steps_merge_by_street_name(straight_route):
    graph, path = straight_route
    labels = [step["label"] for step in route_directions(graph, path)]
    assert labels == ["Dlouha", "Bocni", "Treti"]


def test_step_lengths_sum_to_route_length(straight_route):
    graph, path = straight_route
    steps = route_directions(graph, path)
    assert sum(step["km"] for step in steps) == pytest.approx(TOTAL_M / 1000, abs=1e-6)


def test_steps_start_where_the_previous_one_ends(straight_route):
    graph, path = straight_route
    steps = route_directions(graph, path)
    assert steps[0]["at_km"] == 0
    for previous, step in zip(steps, steps[1:]):
        assert step["at_km"] == pytest.approx(previous["at_km"] + previous["km"], abs=1e-6)


def test_crossing_uses_the_same_kilometrage_as_steps(straight_route):
    """Krizeni u uzlu D lezi 950 m po trase - ne 300 m, kolik dela vzdalenost
    uzlu vzdusnou carou."""
    graph, path = straight_route
    crossings = [c for step in route_directions(graph, path) for c in step["crossings"]]
    assert crossings, "krizeni s Kriznou se ma najit"
    assert crossings[0]["name"] == "Krizna"
    assert crossings[0]["at_km"] == pytest.approx(AT_NODE_M["D"] / 1000, abs=1e-3)


def test_crossing_never_precedes_the_step_it_belongs_to(straight_route):
    graph, path = straight_route
    for step in route_directions(graph, path):
        for crossing in step["crossings"]:
            assert step["at_km"] - 1e-6 <= crossing["at_km"] <= step["at_km"] + step["km"] + 1e-6


def test_parallel_street_is_not_reported_as_crossing(straight_route):
    """Ulice soubezna s trasou neni orientacni bod - smer behu se musi brat
    z trasy, ne z nahodneho souseda uzlu v grafu."""
    graph, path = straight_route
    names = {c["name"] for step in route_directions(graph, path) for c in step["crossings"]}
    assert "Soubezna" not in names


def test_same_crossing_is_not_repeated_across_steps(straight_route):
    """Krizna je u uzlu D i F (300 m od sebe) - beztak je to jedna ulice."""
    graph, path = straight_route
    names = [c["name"] for step in route_directions(graph, path) for c in step["crossings"]]
    assert names.count("Krizna") == 1


def test_no_empty_straight_ahead_instruction(straight_route):
    """Zmena nazvu ulice bez zatoceni neni pokyn - 'rovne' je jen sum."""
    graph, path = straight_route
    assert all(step["turn"] != "rovne" for step in route_directions(graph, path))


def test_first_step_reports_compass_heading(straight_route):
    graph, path = straight_route
    steps = route_directions(graph, path)
    assert steps[0]["start_heading"] == "vychod"
    assert all(step["start_heading"] is None for step in steps[1:])


def test_empty_path_gives_no_directions(straight_route):
    graph, _path = straight_route
    assert route_directions(graph, ["A"]) == []


# --- sber dlazdic (kvuli cemu se cely beh dela) ---

def tile_of(graph, node):
    return lon_lat_tile(graph.nodes[node]["x"], graph.nodes[node]["y"])


def pickups_of(steps):
    return [tile for step in steps for tile in step["tiles"]]


def test_tile_depth_is_measured_from_the_nearest_boundary():
    """Hloubka rozhoduje, jestli se navsteva zapocita i pri chybe GPS."""
    tile = lon_lat_tile(14.42, 50.075)
    west, north = tile_lon_lat(tile[0], tile[1])
    east, south = tile_lon_lat(tile[0] + 1, tile[1] + 1)

    center = _tile_depth_m((north + south) / 2, (west + east) / 2, tile)
    near_edge = _tile_depth_m(south + (north - south) * 0.01, (west + east) / 2, tile)
    assert center > near_edge
    assert near_edge < 100     # tesne u hranice
    assert center > 500        # dlazdice ma v nasich sirkach ~1570 m


def test_nothing_is_reported_without_targets(straight_route):
    graph, path = straight_route
    assert pickups_of(route_directions(graph, path)) == []


def test_only_target_tiles_are_reported(straight_route):
    graph, path = straight_route
    assert pickups_of(route_directions(graph, path, target_tiles=[(1, 1)])) == []
    assert pickups_of(route_directions(graph, path, target_tiles=[tile_of(graph, "A")]))


def test_pickup_carries_where_and_how_deep(straight_route):
    graph, path = straight_route
    pickup = pickups_of(route_directions(graph, path, target_tiles=[tile_of(graph, "A")]))[0]
    assert pickup["at_km"] >= 0
    assert pickup["km"] >= 0
    assert pickup["depth_m"] > 0


def test_no_pickup_is_reported_twice(straight_route):
    """Sousedni kroky sdileji hranicni uzel - pouhy test rozsahu prirazoval
    dlazdici obema (mereno na realne trase: 7 sberu misto 6)."""
    graph, path = straight_route
    steps = route_directions(graph, path, target_tiles=[tile_of(graph, "A")])
    seen = [(tuple(t["tile"]), t["at_km"]) for t in pickups_of(steps)]
    assert len(seen) == len(set(seen))


def test_waypoint_tiles_are_marked(straight_route):
    graph, path = straight_route
    tile = tile_of(graph, "A")
    plain = pickups_of(route_directions(graph, path, target_tiles=[tile]))
    goal = pickups_of(route_directions(graph, path, waypoint_tiles=[tile]))
    assert plain[0]["waypoint"] is False
    assert goal[0]["waypoint"] is True


def test_waypoints_count_as_targets_even_when_not_listed(straight_route):
    """Cilova dlazdice se nesmi ztratit jen proto, ze neni v target_tiles."""
    graph, path = straight_route
    assert pickups_of(route_directions(graph, path, waypoint_tiles=[tile_of(graph, "A")]))


def test_pickup_is_measured_along_the_route_geometry(line_graph):
    """Uzly jsou rozestoupene desitky metru; merit sber po nich hlasilo vjezd
    pozde a delku uvnitr kratsi (7,29 km / 0,22 km misto 7,24 km / 0,27 km).
    Souradnice kopiruji geometrii hran - a jsou to tytez body, ze kterych se
    pocita prinos trasy."""
    tile = lon_lat_tile(14.42, 50.075)
    west, north = tile_lon_lat(tile[0], tile[1])
    east, south = tile_lon_lat(tile[0] + 1, tile[1] + 1)
    lon = (west + east) / 2
    step = (north - south) / 20

    nodes = {"a": (south - step, lon), "b": (south + 10 * step, lon)}
    graph = line_graph(nodes, [("a", "b", {"name": "Dlouha", "length": 2000.0})])
    # trasa mezi uzly prochazi dlazdici - uzel "a" je jeste mimo ni
    coordinates = [(south - step + i * step, lon) for i in range(12)]

    steps = route_directions(graph, ["a", "b"], target_tiles=[tile], coordinates=coordinates)
    pickup = [t for s in steps for t in s["tiles"]][0]
    assert pickup["at_km"] > 0, "vjezd nesmi vyjit na zacatku trasy - uzel 'a' je mimo"
    assert pickup["km"] > 0


# --- rozhodovaci body a odstupnovani odbocek ---

def test_turn_words_are_graded():
    """Bez odstupnovani se mirny ohyb 40 st a vlasenka 140 st cetly stejne."""
    from itinerary import _turn_word

    assert _turn_word(0, 20) is None            # ohyb cesty, ne zatacka
    assert _turn_word(0, 45) == "mirne vpravo"
    assert _turn_word(0, 90) == "vpravo"
    assert _turn_word(0, 130) == "ostre vpravo"
    assert _turn_word(0, 170) == "zpet"


def test_turn_words_tell_the_sides_apart():
    from itinerary import _turn_word

    assert _turn_word(0, 90) == "vpravo"
    assert _turn_word(0, 270) == "vlevo"
    assert _turn_word(90, 180) == "vpravo"


def test_straight_route_has_no_decision_points(straight_route):
    """Rovna trasa: neni kde zabloudit, i kdyz z uzlu B vede bocni ulice."""
    graph, path = straight_route
    assert all(step["decisions"] == [] for step in route_directions(graph, path))


def test_bend_without_alternative_gives_no_instruction(line_graph):
    """Zatacka, ze ktere se neda odbocit jinam, neni rozhodovaci bod."""
    from itinerary import _decision_points

    nodes = {"a": (50.0, 14.40), "b": (50.0, 14.41), "c": (50.01, 14.41)}
    graph = line_graph(nodes, [("a", "b", {}), ("b", "c", {})])
    cumulative = [0.0, 700.0, 1800.0]
    assert _decision_points(graph, ["a", "b", "c"], cumulative) == []


def test_bend_with_an_alternative_is_a_decision_point(line_graph):
    """Tataz zatacka, ale z uzlu vede jeste jina cesta - tam uz se splest da."""
    from itinerary import _decision_points

    nodes = {"a": (50.0, 14.40), "b": (50.0, 14.41), "c": (50.01, 14.41),
             "d": (50.0, 14.42)}
    graph = line_graph(nodes, [("a", "b", {}), ("b", "c", {}), ("b", "d", {})])
    cumulative = [0.0, 700.0, 1800.0]
    points = _decision_points(graph, ["a", "b", "c"], cumulative)
    assert len(points) == 1
    assert points[0]["turn"]


# --- popisky nesmi lhat o tom, kde ulice zacina ---

def labels_of(graph, path):
    return [step["label"] for step in route_directions(graph, path)]


def chain(line_graph, pieces):
    """Rovny retez uzlu; pieces = [(delka_m, nazev_nebo_None), ...]."""
    nodes, edges, lon = {}, [], 14.4
    nodes["n0"] = (LAT, lon)
    for index, (length, name) in enumerate(pieces):
        lon += length * LON_PER_M
        nodes[f"n{index + 1}"] = (LAT, lon)
        data = {"length": float(length)}
        if name:
            data["name"] = name
        edges.append((f"n{index}", f"n{index + 1}", data))
    return line_graph(nodes, edges), [f"n{i}" for i in range(len(pieces) + 1)]


def test_short_gap_does_not_split_a_street(line_graph):
    """Par metru bez nazvu (prechod, spojka) rozdelilo ulici na dva kratke kroky
    a slucovani je pak rozebralo do sousedu - ulice z popisu zmizela. Mereno:
    Oresska 163 m rozseknuta 5metrovym utrzkem na 81 + 77 m."""
    graph, path = chain(line_graph, [(81, "Oresska"), (5, None), (77, "Oresska"),
                                     (150, "Do Vrsku")])
    assert labels_of(graph, path) == ["Oresska", "Do Vrsku"]


def test_street_you_actually_run_along_keeps_its_row(line_graph):
    """Krok se nesmi jmenovat podle ulice, na kterou trasa teprve najede."""
    graph, path = chain(line_graph, [(200, "Pod Vavrincem"), (36, "U Opatrovny"),
                                     (250, "Mezi Lany")])
    assert labels_of(graph, path) == ["Pod Vavrincem", "U Opatrovny", "Mezi Lany"]


def test_brushing_a_corner_is_still_noise(line_graph):
    """Par metru na rohu ulice zustava sumem - jinak by itinerar mel radek na
    kazdou krizovatku (mereno: Puchmajerova 5 m, Walterovo namesti 7 m)."""
    graph, path = chain(line_graph, [(300, "Radlicka"), (7, "Walterovo namesti"),
                                     (300, "Radlicka")])
    assert "Walterovo namesti" not in labels_of(graph, path)


def test_unnamed_stretch_is_not_renamed_after_the_next_street(line_graph):
    """Dlouhy bezejmenny chodnik pred Plzenskou se nesmi jmenovat Plzenska."""
    graph, path = chain(line_graph, [(69, None), (33, None), (79, None), (250, "Plzenska")])
    labels = labels_of(graph, path)
    assert labels[0] != "Plzenska"
    assert "Plzenska" in labels


# --- znacena trasa nesmi platit pro cely usek, kdyz odbocuje ---

def marked(line_graph, pieces):
    """Retez uzlu, kde kazdy usek muze nest znacku: (delka, nazev, znacka)."""
    nodes, edges, lon = {}, [], 14.4
    nodes["n0"] = (LAT, lon)
    for index, (length, name, trail) in enumerate(pieces):
        lon += length * LON_PER_M
        nodes[f"n{index + 1}"] = (LAT, lon)
        data = {"length": float(length)}
        if name:
            data["name"] = name
        if trail:
            data["trail"] = trail
        edges.append((f"n{index}", f"n{index + 1}", data))
    graph = line_graph(nodes, edges)
    return graph, [f"n{i}" for i in range(len(pieces) + 1)]


def test_trail_over_the_whole_step_needs_no_range(line_graph):
    graph, path = marked(line_graph, [(300, "Lesni", "zelena turisticka"),
                                      (300, "Lesni", "zelena turisticka")])
    step = route_directions(graph, path)[0]
    assert step["trail"] == "zelena turisticka"
    assert step["trail_km"] is None


def test_trail_that_leaves_mid_step_carries_its_range(line_graph):
    """Kdyz itinerar pripise znacku celemu useku, ale ona v polovine odbocí,
    bezec ji poslechne a odbocí taky. Mereno na vyprave do Zbuzan: zelena
    pokryvala 295 m ze 670metroveho useku a byla uvedena pro cely."""
    graph, path = marked(line_graph, [(300, "Lesni", "zelena turisticka"),
                                      (400, "Lesni", None)])
    step = route_directions(graph, path)[0]
    assert step["trail"] == "zelena turisticka"
    assert step["trail_km"] == [0.0, 0.3]


def test_barely_present_trail_is_not_reported(line_graph):
    graph, path = marked(line_graph, [(40, "Lesni", "zelena turisticka"),
                                      (600, "Lesni", None)])
    assert route_directions(graph, path)[0]["trail"] is None


def test_absorbed_step_keeps_its_trail_in_order(line_graph):
    """names/trails musi zustat zarovnane s uzly i pri slucovani dozadu -
    jinak rozsah znacky ukazuje na spatne misto."""
    graph, path = marked(line_graph, [(20, None, "zelena turisticka"),
                                      (400, "Lesni", "zelena turisticka"),
                                      (400, "Lesni", None)])
    step = route_directions(graph, path)[0]
    assert step["trail_km"][0] == 0.0


# --- fragmentace: sousedni kroky s touz informaci ---

def test_path_along_a_street_merges_with_the_street(line_graph):
    """Pesina podel Novoveske a Novoveska sama jsou jeden usek - meni se jen
    charakter cesty, ne kudy se bezi."""
    graph, path = marked(line_graph, [(200, None, None), (600, "Novoveska", None)])
    for _u, _v, data in graph.edges(data=True):
        if not data.get("name"):
            data["highway"], data["along_street"] = "path", "Novoveska"
    assert labels_of(graph, path) == ["Novoveska"]


def test_path_that_only_passes_a_street_keeps_its_own_description(line_graph):
    """Pesina, ktera se na chvili priblizi Jitrocelove, zustava pesinou -
    z nahodne soubeznosti se nesmi stat nazev useku."""
    graph, path = marked(line_graph, [(300, None, None), (200, None, None),
                                      (300, None, None)])
    for index, (_u, _v, data) in enumerate(graph.edges(data=True)):
        data["highway"] = "path"
        if index in (2, 3):        # prostredni usek (obe orientace hrany)
            data["along_street"] = "Jitrocelova"
    labels = labels_of(graph, path)
    assert labels == ["pesina"], labels


def test_steps_on_the_same_hiking_trail_become_one(line_graph):
    """V CR jsou turisticke znacky spolehlive - kdo bezi po zlute, sleduje
    znacky, ne cedule s nazvy ulic. Mereno na vyprave do Zbuzan: zluta byla
    roztristena do sedmi radku podle ulic."""
    graph, path = marked(line_graph, [(300, "Pod Vavrincem", "zluta turisticka"),
                                      (300, "Mezi Lany", "zluta turisticka"),
                                      (300, "Radlicka", "zluta turisticka")])
    steps = route_directions(graph, path)
    assert [s["label"] for s in steps] == ["zluta turisticka"]
    assert steps[0]["via"] == ["Pod Vavrincem", "Mezi Lany", "Radlicka"]


def test_cycle_routes_do_not_swallow_street_names(line_graph):
    """Cyklotrasy nejsou v terenu znacene tak spolehlive jako turisticke -
    nazvy ulic zustavaji popisem."""
    graph, path = marked(line_graph, [(300, "Prvni", "cyklotrasa A12"),
                                      (300, "Druha", "cyklotrasa A12")])
    assert [s["label"] for s in route_directions(graph, path)] == ["Prvni", "Druha"]


def test_marking_without_a_colour_is_ignored(line_graph):
    """V CR nejsou jine turisticke znacky nez barevne nebo naucne stezky -
    genericka "turisticka znacka" pochazi z relaci bez osmc:symbol a jen mate."""
    graph, path = marked(line_graph, [(400, "Peroutkova", "turisticka znacka")])
    assert route_directions(graph, path)[0]["trail"] is None
