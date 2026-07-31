"""Trasy metra pro podklad v mape (turisticka vrstva Mapy.cz je nekresli)."""
import pytest

from transit import METRO_COLORS, metro_geometry

# Zastavka ma v GTFS vlastni uzel pro kazde nastupiste; hrany jsou
# [z, do, minuty, linka, druh, spoj].
GRAPH = {
    "stops": {
        "A1": ["Dejvická", 50.100, 14.393],
        "A2": ["Dejvická", 50.1002, 14.3932],
        "M1": ["Malostranská", 50.090, 14.404],
        "M2": ["Malostranská", 50.0902, 14.4042],
        "U1": ["Muzeum", 50.079, 14.430],
        "U2": ["Muzeum", 50.0792, 14.4302],
        "T1": ["I. P. Pavlova", 50.075, 14.430],
        "B1": ["Anděl", 50.070, 14.403],
    },
    "edges": [
        ["A1", "M1", 2.0, "A", "metro", "L991|0"],
        ["M2", "A2", 2.0, "A", "metro", "L991|1"],   # opacny smer teze trate
        ["M1", "U1", 3.0, "A", "metro", "L991|0"],
        ["U2", "M2", 3.0, "A", "metro", "L991|1"],
        ["U1", "T1", 1.5, "C", "metro", "L993|0"],
        ["B1", "A1", 5.0, "12", "tram", "L12|0"],    # tramvaj do metra nepatri
    ],
}


@pytest.fixture
def metro():
    return metro_geometry(GRAPH)


def test_only_metro_edges_are_used(metro):
    assert {line["line"] for line in metro["lines"]} == {"A", "C"}
    assert "Anděl" not in {station["name"] for station in metro["stations"]}


def test_both_directions_collapse_into_one_segment(metro):
    """Kazdy usek je v GTFS dvakrat (tam a zpet) - v mape ma byt jednou."""
    line_a = next(line for line in metro["lines"] if line["line"] == "A")
    assert len(line_a["segments"]) == 2


def test_platforms_of_one_station_merge(metro):
    """Bez slouceni podle nazvu by kazda linka vysla jako dve rovnobezky."""
    names = [station["name"] for station in metro["stations"]]
    assert names.count("Dejvická") == 1
    dejvicka = next(s for s in metro["stations"] if s["name"] == "Dejvická")
    assert dejvicka["lat"] == pytest.approx(50.1001, abs=1e-4)


def test_segments_connect_real_station_positions(metro):
    line_a = next(line for line in metro["lines"] if line["line"] == "A")
    positions = {(station["lat"], station["lon"]) for station in metro["stations"]}
    for segment in line_a["segments"]:
        for point in segment:
            assert tuple(point) in positions


def test_transfer_station_lists_all_its_lines(metro):
    muzeum = next(s for s in metro["stations"] if s["name"] == "Muzeum")
    assert muzeum["lines"] == ["A", "C"]


def test_lines_carry_their_own_colour(metro):
    for line in metro["lines"]:
        assert line["color"] == METRO_COLORS[line["line"]]


def test_unknown_line_still_gets_a_colour():
    graph = {
        "stops": {"X1": ["Depo", 50.0, 14.0], "X2": ["Vozovna", 50.01, 14.01]},
        "edges": [["X1", "X2", 1.0, "D", "metro", "L994|0"]],
    }
    assert metro_geometry(graph)["lines"][0]["color"]


def test_network_without_metro_gives_nothing():
    graph = {
        "stops": {"B1": ["Anděl", 50.070, 14.403], "B2": ["Zborovská", 50.075, 14.410]},
        "edges": [["B1", "B2", 2.0, "12", "tram", "L12|0"]],
    }
    result = metro_geometry(graph)
    assert result["lines"] == []
    assert result["stations"] == []
