"""Cena hran pro beh: poradi preferenci typu cest a kontext, ve kterem cesta vede."""
import pytest

from runcost import RUN_PREFERENCES, prepare_run_costs


def cost_of(line_graph, **tags):
    """Cena za metr jedne hrany s danymi tagy."""
    nodes = {1: (50.0, 14.4), 2: (50.0, 14.41)}
    tags.setdefault("length", 1000.0)
    graph = line_graph(nodes, [(1, 2, tags)])
    prepare_run_costs(graph)
    edge = graph[1][2][0]
    return edge["run_cost"] / edge["length"]


def test_preference_order_matches_how_it_runs():
    """Dedikovana cyklostezka > pesina/polni cesta > pesi zona > chodnik >
    klidna ulice > rusna silnice (preference uzivatele)."""
    order = ["cycleway", "path", "pedestrian", "footway", "living_street",
             "residential", "tertiary", "secondary", "primary", "trunk"]
    factors = [RUN_PREFERENCES[highway] for highway in order]
    assert factors == sorted(factors)


def test_steps_are_penalised():
    assert RUN_PREFERENCES["steps"] > RUN_PREFERENCES["footway"]


def test_unknown_way_type_gets_a_neutral_cost(line_graph):
    assert cost_of(line_graph, highway="via_ferrata") == pytest.approx(1.1)


def test_multi_valued_highway_tag_takes_the_best(line_graph):
    assert cost_of(line_graph, highway=["footway", "secondary"]) == pytest.approx(
        RUN_PREFERENCES["footway"]
    )


def test_sidewalk_along_busy_street_loses_to_a_quiet_street(line_graph):
    """Jadro problemu: chodnik podel ctyrproude silnice neni prijemna cesta.
    Bez tohoto rozliseni je pro plánovac kazdy chodnik stejne dobry."""
    busy_sidewalk = cost_of(line_graph, highway="footway", along_major=True)
    quiet_street = cost_of(line_graph, highway="residential")
    assert busy_sidewalk > quiet_street


def test_sidewalk_off_a_busy_street_keeps_its_advantage(line_graph):
    """Penalizace se smi tykat jen chodniku podel VYZNAMNE ulice - jinak by
    zdrazila i cesty parkem."""
    assert cost_of(line_graph, highway="footway") < cost_of(line_graph, highway="residential")
    assert (cost_of(line_graph, highway="footway", along_major=False)
            == pytest.approx(RUN_PREFERENCES["footway"]))


def test_context_never_makes_an_edge_cheaper(line_graph):
    """Schody podel rusne ulice zustavaji schody."""
    assert (cost_of(line_graph, highway="steps", along_major=True)
            >= RUN_PREFERENCES["steps"])


def test_marked_trail_is_rewarded(line_graph):
    """Turisticka znacka/cyklotrasa je pro beh bonus - a uz se pocita
    pri priprave grafu, takze ji smi vyuzit i hledani cesty."""
    assert (cost_of(line_graph, highway="path", trail="cervena turisticka")
            < cost_of(line_graph, highway="path"))


def test_trail_does_not_rehabilitate_a_busy_sidewalk(line_graph):
    """Cyklotrasa vedena po chodniku podel magistraly je porad chodnik
    podel magistraly."""
    assert (cost_of(line_graph, highway="footway", along_major=True, trail="cyklotrasa A2")
            > cost_of(line_graph, highway="footway", trail="cyklotrasa A2"))


def test_cost_is_proportional_to_length(line_graph):
    nodes = {1: (50.0, 14.4), 2: (50.0, 14.41)}
    graph = line_graph(nodes, [(1, 2, {"highway": "residential", "length": 250.0})])
    prepare_run_costs(graph)
    assert graph[1][2][0]["run_cost"] == pytest.approx(250.0 * RUN_PREFERENCES["residential"])
