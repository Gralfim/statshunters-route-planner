"""Itinerar behu: kilometraz, orientacni body, pokyny k zataceni.

Graf je rucne postaveny primy usek na 50. rovnobezce, kde je delka hran zadana
NEZAVISLE na vzdalenosti uzlu (v OSM `length` kopiruje geometrii ulice, kdezto
uzly jsou jen krizovatky). Prave na tom rozdilu se poznaji chyby kilometraze.
"""
import math

import pytest

from routing import route_directions

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
