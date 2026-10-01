"""Ktere cesty pesi graf obsahuje (waygraph.WALK_FILTERS).

Filtr `walk` z osmnx vyrazuje highway=cycleway vzdy, i spolecnou stezku pro
chodce a cyklisty. Graf pak nemel ani metr cyklostezek a planovac objizdel
(rucni trasa Stodulky -> Barrandov: 14 % delky mimo graf, tytez dlazdice za
26,5 km misto 17,1 km). Filtry se tu vyhodnocuji na tagech skutecnych cest,
ne porovnanim retezcu - test ma hlidat, CO v grafu je.
"""
import re

import pytest

import waygraph

CONDITION = re.compile(r'\["([^"]+)"(?:(=|!=|~|!~)"([^"]*)")?\]')


def matches(overpass_filter, tags):
    """Vyhodnoti retezec podminek Overpass QL ["k"], ["k"="v"], ["k"~"re"],
    ["k"!~"re"] nad tagy jedne cesty (vsechny podminky plati zaroven)."""
    for key, operator, value in CONDITION.findall(overpass_filter):
        present = key in tags
        if not operator:
            ok = present
        elif operator == "=":
            ok = present and tags[key] == value
        elif operator == "!=":
            ok = not present or tags[key] != value
        elif operator == "~":
            ok = present and re.search(value, tags[key]) is not None
        else:  # !~ plati i tam, kde tag chybi
            ok = not present or re.search(value, tags[key]) is None
        if not ok:
            return False
    return True


def in_graph(**tags):
    return any(matches(query, tags) for query in waygraph.WALK_FILTERS)


@pytest.mark.parametrize("tags", [
    # spolecna stezka pro chodce a cyklisty - presne ty useky chybely
    {"highway": "cycleway", "foot": "designated", "bicycle": "designated", "segregated": "no"},
    {"highway": "cycleway", "foot": "yes"},
    {"highway": "cycleway", "foot": "permissive"},
    # co bylo v grafu uz predtim, v nem zustava
    {"highway": "footway"},
    {"highway": "path", "foot": "designated", "bicycle": "designated"},
    {"highway": "track", "tracktype": "grade2"},
    {"highway": "residential"},
])
def test_walkable_ways_are_in_the_graph(tags):
    assert in_graph(**tags)


@pytest.mark.parametrize("tags", [
    # cista cyklostezka: chodec tam nepatri, i kdyz ma znacku foot vubec nema
    {"highway": "cycleway"},
    {"highway": "cycleway", "foot": "no"},
    {"highway": "cycleway", "foot": "designated", "access": "private"},
    # to, co filtr walk vyrazoval, vyrazuje dal
    {"highway": "secondary", "foot": "no"},
    {"highway": "footway", "access": "private"},
    {"highway": "motorway"},
    {"highway": "secondary", "sidewalk": "separate"},
    {"highway": "construction"},
])
def test_ways_a_runner_may_not_use_stay_out(tags):
    assert not in_graph(**tags)


def test_graphs_downloaded_with_the_old_filter_are_not_reused(tmp_path, monkeypatch):
    """Cache je podle pokryti - stary graf bez cyklostezek by jinak pokryval
    oblast a pouzival se dal. Verzi nese predpona souboru."""
    monkeypatch.setattr(waygraph, "GRAPH_DIR", tmp_path)
    (tmp_path / "walk_50.075_14.420_15.0km.graphml").write_text("", encoding="utf-8")
    assert waygraph.covering_graph_path(50.075, 14.420, 9.5) is None

    current = waygraph.graph_path(50.075, 14.420, 15.0)
    current.write_text("", encoding="utf-8")
    assert waygraph.covering_graph_path(50.075, 14.420, 9.5) == current
