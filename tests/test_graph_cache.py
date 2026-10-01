"""Cache pripraveneho grafu (pickle).

Zradna cast je zneplatneni: kdyby se po zmene preferenci pouzil ulozeny graf se
starymi cenami, trasy by se tise planovaly podle nastaveni, ktere uz v kodu
neni - a nic by na to neupozornilo.
"""
import pytest

import runcost
import waygraph


@pytest.fixture
def fake_graphml(tmp_path):
    """Prazdny soubor jen kvuli jmenu - cesty k cache se z nej jen odvozuji."""
    path = tmp_path / "walk_50.076_14.419_9.5km.graphml"
    path.write_text("", encoding="utf-8")
    return path


def test_cache_path_sits_next_to_its_graph(fake_graphml):
    """Nazvy grafu obsahuji tecky, takze se cesta nesmi skladat pres with_suffix."""
    cached = waygraph._prepared_cache_path(fake_graphml)
    assert cached.parent == fake_graphml.parent
    assert cached.name.startswith("walk_50.076_14.419_9.5km.prepared-")
    assert cached.suffix == ".pkl"


def test_fingerprint_is_stable():
    assert waygraph._preparation_fingerprint() == waygraph._preparation_fingerprint()


@pytest.mark.parametrize("attribute, value", [
    ("ALONG_MAJOR_FACTOR", 1.9),
    ("TRAIL_BONUS", 0.5),
    ("DEFAULT_RUN_FACTOR", 2.0),
])
def test_changing_a_cost_parameter_invalidates_the_cache(monkeypatch, attribute, value):
    before = waygraph._preparation_fingerprint()
    monkeypatch.setattr(runcost, attribute, value)
    assert waygraph._preparation_fingerprint() != before


def test_changing_way_preferences_invalidates_the_cache(monkeypatch):
    before = waygraph._preparation_fingerprint()
    monkeypatch.setitem(runcost.RUN_PREFERENCES, "footway", 0.42)
    assert waygraph._preparation_fingerprint() != before


def test_changing_street_matching_invalidates_the_cache(monkeypatch):
    """Prahy parovani meni along_street/along_major, tedy i vysledne ceny."""
    before = waygraph._preparation_fingerprint()
    monkeypatch.setattr(waygraph, "STREET_MATCH_MAX_M", 99.0)
    assert waygraph._preparation_fingerprint() != before


def test_prepared_graph_survives_the_round_trip(fake_graphml, line_graph):
    graph = line_graph(
        {1: (50.0, 14.4), 2: (50.0, 14.41)},
        [(1, 2, {"highway": "footway", "along_street": "Jecna", "along_major": True})],
    )
    runcost.prepare_run_costs(graph)

    waygraph._store_prepared(fake_graphml, graph)
    restored = waygraph._load_prepared(fake_graphml)

    assert restored is not None
    edge = restored[1][2][0]
    assert edge["run_cost"] == graph[1][2][0]["run_cost"]
    assert edge["along_street"] == "Jecna"
    assert edge["along_major"] is True


def test_stale_fingerprints_are_removed(fake_graphml, line_graph):
    stale = fake_graphml.parent / f"{fake_graphml.stem}.prepared-deadbeef.pkl"
    stale.write_bytes(b"stary otisk")

    graph = line_graph({1: (50.0, 14.4), 2: (50.0, 14.41)}, [(1, 2, {})])
    runcost.prepare_run_costs(graph)
    waygraph._store_prepared(fake_graphml, graph)

    assert not stale.exists()
    assert waygraph._prepared_cache_path(fake_graphml).exists()


def test_no_temporary_file_is_left_behind(fake_graphml, line_graph):
    graph = line_graph({1: (50.0, 14.4), 2: (50.0, 14.41)}, [(1, 2, {})])
    runcost.prepare_run_costs(graph)
    waygraph._store_prepared(fake_graphml, graph)
    assert not list(fake_graphml.parent.glob("*.tmp"))


def test_corrupted_cache_is_discarded_not_raised(fake_graphml):
    """Poskozena cache nesmi shodit planovani - jen se zahodi a prepocita."""
    cached = waygraph._prepared_cache_path(fake_graphml)
    cached.write_bytes(b"tohle neni pickle")

    assert waygraph._load_prepared(fake_graphml) is None
    assert not cached.exists()


def test_missing_cache_is_simply_absent(fake_graphml):
    assert waygraph._load_prepared(fake_graphml) is None


def test_cache_of_another_fingerprint_is_not_used(fake_graphml, line_graph, monkeypatch):
    graph = line_graph({1: (50.0, 14.4), 2: (50.0, 14.41)}, [(1, 2, {})])
    runcost.prepare_run_costs(graph)
    waygraph._store_prepared(fake_graphml, graph)
    assert waygraph._load_prepared(fake_graphml) is not None

    monkeypatch.setattr(runcost, "ALONG_MAJOR_FACTOR", 1.9)
    assert waygraph._load_prepared(fake_graphml) is None


def test_pickle_keeps_the_street_index(fake_graphml, line_graph):
    """street_segments jsou numpy pole na grafu - bez nich by itinerar prisel
    o orientacni body."""
    import numpy as np

    graph = line_graph({1: (50.0, 14.4), 2: (50.0, 14.41)}, [(1, 2, {})])
    graph.graph["street_segments"] = (
        np.array([50.0]), np.array([14.4]), np.array([90.0]),
        np.array(["Jecna"], dtype=object), np.array([True]),
    )
    runcost.prepare_run_costs(graph)
    waygraph._store_prepared(fake_graphml, graph)

    segments = waygraph._load_prepared(fake_graphml).graph["street_segments"]
    assert segments[3][0] == "Jecna"
    assert bool(segments[4][0]) is True


# --- vypadek zdroje pri priprave grafu ---

def _two_node_graph(line_graph):
    return line_graph({1: (50.0, 14.0), 2: (50.001, 14.0)}, [(1, 2, {"highway": "footway"})])


def test_graph_prepared_without_streets_is_incomplete(line_graph, monkeypatch):
    """Drive se za neuplny povazoval jen graf bez znacenych tras. Bez ulic ale
    chodniky nemaji jmeno ani priznak "podel rusne ulice" (meni se ceny hran
    i itinerar) - a takovy graf se ulozil do cache natrvalo."""
    import landmarks

    graph = _two_node_graph(line_graph)
    monkeypatch.setattr(landmarks, "load_streets", lambda *_args: None)
    monkeypatch.setattr(landmarks, "load_trails", lambda *_args: [])
    _graph, complete = waygraph._prepare(graph, 50.0, 14.0, 10)
    assert not complete
    assert graph.graph["sources_complete"] is False


def test_an_empty_area_is_complete_unlike_an_outage(line_graph, monkeypatch):
    """Prazdny seznam = v okoli nic neni (platny vysledek), None = vypadek."""
    import landmarks

    graph = _two_node_graph(line_graph)
    monkeypatch.setattr(landmarks, "load_streets", lambda *_args: [])
    monkeypatch.setattr(landmarks, "load_trails", lambda *_args: [])
    _graph, complete = waygraph._prepare(graph, 50.0, 14.0, 10)
    assert complete


def test_incomplete_graph_in_memory_is_completed_and_cached_later(tmp_path, line_graph,
                                                                 monkeypatch):
    """Neuplny graf zustava v pameti; pri dalsim nacteni se zdroj zkusi znovu
    a do cache se graf ulozi, az kdyz je uplny."""
    import landmarks

    path = tmp_path / f"{waygraph.GRAPH_PREFIX}_50.000_14.000_10.0km.graphml"
    path.write_text("", encoding="utf-8")
    graph = _two_node_graph(line_graph)
    graph.graph["sources_complete"] = False
    monkeypatch.setattr(waygraph, "GRAPH_DIR", tmp_path)
    monkeypatch.setattr(waygraph, "_GRAPH_MEMORY", {str(path): graph})
    monkeypatch.setattr(landmarks, "load_streets", lambda *_args: [])
    monkeypatch.setattr(landmarks, "load_trails", lambda *_args: [])

    assert waygraph.load_walk_graph(50.0, 14.0, 5) is graph
    assert graph.graph["sources_complete"] is True
    assert waygraph._load_prepared(path) is not None
