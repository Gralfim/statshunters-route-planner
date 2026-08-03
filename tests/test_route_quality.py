"""Kontrolni mereni kvality trasy na SKUTECNEM grafu Prahy.

Bezi jen na vyzadani (`pytest -m slow`): potrebuje stazeny pesi graf a data
aktivit a trva pres minutu (nacteni grafu ~30 s, scoring ~9 s, planovani ~10 s).
Smysl: cenovy model se ladi cisly, ne pocitem - kdyby se penalizace chodniku
podel vyznamnych ulic rozbila nebo nekdo prehodil poradi v _prepare (ceny se
musi pocitat AZ po obohaceni), tenhle test to chyti. Male testy nad rucnim
grafem to nezachyti, protoze tam zadne magistraly nejsou.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Namerene na referencnim okruhu 15+-3 km z Karlova namesti (07/2026).
# Pred zavedenim ALONG_MAJOR_FACTOR vedlo podel vyznamnych ulic 62,9 % delky.
HOME = (50.0745, 14.4201)
DISTANCE_KM, TOLERANCE_KM = 15.0, 3.0
MAX_ALONG_MAJOR_PCT = 35.0
MIN_QUIET_PCT = 55.0
# Delka smi od cile utect nejvys o polovinu tolerance. Neni to tvrde pravidlo -
# cilova funkce smi delsi trasu vybrat, kdyz ji vyplati prinosem (mereno u cile
# 20 km: 22,5 km s prinosem 410 porazilo 20,0 km s prinosem 308) - ale na
# referencnim okruhu 15+-3 km vychazi odchylka 0,07 km, takze regrese by byla
# poznat okamzite.
MAX_TARGET_DEVIATION_KM = TOLERANCE_KM / 2

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def planned_route():
    import routeplan
    from api import get_period_tile_database
    from routeplan import plan_tile_loop
    from scoring import build_route_context, find_tile_opportunities
    from waygraph import load_walk_graph

    if not any((ROOT / "data").glob("walk_*km.graphml")):
        pytest.skip("v data/ neni zadny stazeny pesi graf")

    reach_km = (DISTANCE_KM + TOLERANCE_KM) / 2 + 0.5
    graph = load_walk_graph(HOME[0], HOME[1], reach_km)
    if graph.graph.get("street_segments") is None:
        pytest.skip("graf nema index ulic - bez nej se kontext cest neda merit")

    tile_dbs = {key: get_period_tile_database(key) for key in ("all", "year", "recent")}

    # node_path v odpovedi neni (do UI nepatri), ale mereni ho potrebuje.
    # Patchuje se jmeno v routeplan, kde se pouziva - ne v itinerary, odkud
    # pochazi (plan_tile_loop si ho hleda mezi svymi globaly).
    captured = {}
    original = routeplan.route_directions

    def capture(g, node_path, **kwargs):
        captured["node_path"] = node_path
        return original(g, node_path, **kwargs)

    routeplan.route_directions = capture
    try:
        route = plan_tile_loop(
            graph, HOME[0], HOME[1], DISTANCE_KM, TOLERANCE_KM,
            find_tile_opportunities(tile_dbs), build_route_context(tile_dbs),
        )
    finally:
        routeplan.route_directions = original

    return graph, route, captured["node_path"]


def surface_mix(graph, node_path):
    """Podil delky trasy podle toho, kudy vede (v procentech)."""
    from geo import tag
    from runcost import best_edge

    quiet_types = {"footway", "path", "track", "cycleway", "pedestrian",
                   "living_street", "residential"}
    total = along_major = quiet = trail = 0.0
    for u, v in zip(node_path, node_path[1:]):
        edge = best_edge(graph, u, v)
        length = float(edge["length"])
        total += length
        if edge.get("along_major"):
            along_major += length
        elif tag(edge, "highway") in quiet_types:
            quiet += length
        if edge.get("trail"):
            trail += length
    return {"along_major": 100 * along_major / total, "quiet": 100 * quiet / total,
            "trail": 100 * trail / total, "km": total / 1000}


def test_route_avoids_sidewalks_along_busy_streets(planned_route):
    graph, _route, node_path = planned_route
    mix = surface_mix(graph, node_path)
    assert mix["along_major"] < MAX_ALONG_MAJOR_PCT, (
        f"podel vyznamnych ulic vede {mix['along_major']:.1f} % trasy "
        f"(limit {MAX_ALONG_MAJOR_PCT} %) - zkontroluj ALONG_MAJOR_FACTOR "
        f"a poradi enrich_streets/prepare_run_costs v _prepare"
    )


def test_route_runs_mostly_on_quiet_ways(planned_route):
    graph, _route, node_path = planned_route
    mix = surface_mix(graph, node_path)
    assert mix["quiet"] > MIN_QUIET_PCT, f"klidnych cest jen {mix['quiet']:.1f} %"


def test_route_length_respects_the_tolerance(planned_route):
    _graph, route, _node_path = planned_route
    assert DISTANCE_KM - TOLERANCE_KM <= route["length_km"] <= DISTANCE_KM + TOLERANCE_KM
    assert route["within_target"]


def test_route_does_not_drift_to_the_upper_bound(planned_route):
    """Delsi trasa protne vic dlazdic, takze bez penalizace odchylky vitezily
    trasy u horni hranice tolerance - z obalky splnitelnosti se stala
    preference."""
    _graph, route, _node_path = planned_route
    deviation = abs(route["length_km"] - DISTANCE_KM)
    assert deviation <= MAX_TARGET_DEVIATION_KM, (
        f"trasa {route['length_km']} km je {deviation:.2f} km od cile {DISTANCE_KM} km "
        f"(limit {MAX_TARGET_DEVIATION_KM}) - zkontroluj LENGTH_PENALTY_FRACTION "
        f"a _shrink_toward_target"
    )


def test_itinerary_kilometrage_is_consistent(planned_route):
    """Na skutecne trase (klikate geometrie, stovky uzlu) musi sedet soucet
    kroku s delkou trasy a kazde krizeni lezet uvnitr sveho useku."""
    _graph, route, _node_path = planned_route
    steps = route["directions"]
    assert sum(step["km"] for step in steps) == pytest.approx(route["length_km"], abs=0.05)
    for step in steps:
        for crossing in step["crossings"]:
            assert step["at_km"] - 0.01 <= crossing["at_km"] <= step["at_km"] + step["km"] + 0.01


def test_itinerary_reports_each_crossing_once(planned_route):
    """Tataz ulice se smi objevit znovu, az kdyz ji trasa opravdu krizi jinde
    (okruh se casto vraci pres stejny tah) - ne o par desitek metru dal."""
    from itinerary import CROSSING_DEDUP_M

    _graph, route, _node_path = planned_route
    last_seen = {}
    for step in route["directions"]:
        for crossing in step["crossings"]:
            previous = last_seen.get(crossing["name"])
            assert previous is None or crossing["at_km"] - previous >= CROSSING_DEDUP_M / 1000, (
                f"{crossing['name']} hlasena dvakrat behem "
                f"{1000 * (crossing['at_km'] - previous):.0f} m"
            )
            last_seen[crossing["name"]] = crossing["at_km"]


def test_itinerary_has_no_empty_instructions(planned_route):
    _graph, route, _node_path = planned_route
    assert all(step["turn"] != "rovne" for step in route["directions"])


def test_route_reports_the_measures_it_was_chosen_by(planned_route):
    """Bez techto cisel v odpovedi nejde posuvnik v UI ladit ani poznat, co
    zmenil."""
    _graph, route, _node_path = planned_route
    assert 0 <= route["along_major_share"] <= 1
    assert route["along_major_km"] <= route["length_km"]
    assert route["score"] <= route["benefit"]["total"] + 1e-6
    assert 0 <= route["quiet_weight"] <= 1


@pytest.fixture(scope="module")
def slider_ends(planned_route):
    """Trasa pri nulove a plne vaze klidu (drahe - planuje se dvakrat)."""
    graph, _route, _node_path = planned_route
    from api import get_period_tile_database
    from routeplan import plan_tile_loop
    from scoring import build_route_context, find_tile_opportunities

    tile_dbs = {key: get_period_tile_database(key) for key in ("all", "year", "recent")}
    opportunities = find_tile_opportunities(tile_dbs)
    context = build_route_context(tile_dbs)

    return {
        weight: plan_tile_loop(graph, HOME[0], HOME[1], DISTANCE_KM, TOLERANCE_KM,
                               opportunities, context, quiet_weight=weight)
        for weight in (0.0, 1.0)
    }


def test_quiet_weight_avoids_busy_streets(slider_ends):
    """Posuvnik musi neco delat: pri plne vaze klidu nesmi trasa vest podel
    vyznamnych ulic vic nez pri nulove."""
    quiet, loud = slider_ends[1.0], slider_ends[0.0]
    assert quiet["along_major_share"] <= loud["along_major_share"] + 1e-9, (
        f"plny klid dal {100*quiet['along_major_share']:.1f} % podel vyznamnych ulic, "
        f"nulovy {100*loud['along_major_share']:.1f} %"
    )


def test_quiet_weight_seeks_marked_trails(slider_ends):
    """Duvod, proc do cilove funkce pribyl clen za znacene trasy: bez nej
    posuvnik jen ubiral posledni metry chodniku u magistraly a trasa se timhle
    smerem temer nemenila."""
    quiet, loud = slider_ends[1.0], slider_ends[0.0]
    assert quiet["trail_share"] >= loud["trail_share"], (
        f"plny klid dal {100*quiet['trail_share']:.1f} % po znackach, "
        f"nulovy {100*loud['trail_share']:.1f} %"
    )


def test_slider_ends_give_visibly_different_routes(slider_ends):
    """Uzivatelska stiznost, kvuli ktere clen za znacene trasy vznikl: 'zadne
    rozdily nevidim'. Krajni polohy posuvniku musi dat jinou trasu, ne tutez
    o par metru - meri se podilem SPOLECNYCH metru."""
    quiet, loud = slider_ends[1.0], slider_ends[0.0]
    shared = set(map(tuple, quiet["coordinates"])) & set(map(tuple, loud["coordinates"]))
    overlap = len(shared) / len(quiet["coordinates"])
    assert overlap < 0.8, f"krajni polohy posuvniku sdileji {100*overlap:.0f} % bodu trasy"
