"""Bodovani prinosu: vahy priorit, spolecny prinos mnoziny, staleness."""
from datetime import date, datetime

import pytest

from scoring import (PRIORITIES, PRIORITY_WEIGHTS, build_route_context,
                     evaluate_tile_set, find_tile_opportunities)

TODAY = date(2026, 7, 29)
OLD = datetime(2020, 1, 1)


def context_for(all_tiles, year_tiles=None, recent_tiles=None, last_visit=OLD):
    """Kontext ze tri obdobi; year/recent defaultne kopiruji all."""
    def db(tiles):
        return {tile: {"last_visit": last_visit, "first_visit": last_visit,
                       "visit_count": 1} for tile in tiles}

    return build_route_context(
        {
            "all": db(all_tiles),
            "year": db(all_tiles if year_tiles is None else year_tiles),
            "recent": db(all_tiles if recent_tiles is None else recent_tiles),
        },
        today=TODAY,
    )


def test_priority_weights_keep_period_order():
    """Nejslabsi priorita delsiho obdobi musi prebit nejsilnejsi priorita
    kratsiho - jinak by 3mesicni metriky prehlusily celkove."""
    assert PRIORITY_WEIGHTS["all_unvisited"] > PRIORITY_WEIGHTS["year_square"]
    assert PRIORITY_WEIGHTS["year_unvisited"] > PRIORITY_WEIGHTS["recent_square"]


def test_priority_weights_keep_kind_order_inside_period():
    for period in ("all", "year", "recent"):
        assert (PRIORITY_WEIGHTS[f"{period}_square"]
                > PRIORITY_WEIGHTS[f"{period}_cluster"]
                > PRIORITY_WEIGHTS[f"{period}_unvisited"])


def test_visiting_nothing_new_has_no_gain():
    tiles = {(0, 0), (1, 0)}
    result = evaluate_tile_set(tiles, context_for(tiles))
    assert all(gain == 0 for gain in result["gains"].values())


def test_unvisited_tile_counts_in_every_period():
    result = evaluate_tile_set({(5, 5)}, context_for({(0, 0)}))
    assert result["gains"]["all_unvisited"] == 1
    assert result["gains"]["year_unvisited"] == 1
    assert result["gains"]["recent_unvisited"] == 1


def test_set_gain_is_not_additive_over_tiles():
    """Dva tiles dokompletuji square 2x2, samostatne ani jeden nic nezvetsi -
    kvuli tomu se prinos pocita nad celou mnozinou najednou."""
    existing = {(0, 0), (1, 0)}
    context = context_for(existing)
    missing = [(0, 1), (1, 1)]

    alone = [evaluate_tile_set({tile}, context)["gains"]["all_square"] for tile in missing]
    together = evaluate_tile_set(set(missing), context)["gains"]["all_square"]

    assert alone == [0, 0]
    assert together == 1


def test_square_is_weighted_by_area_not_side():
    """Rust strany square (vzacny) musi prebit rust clusteru o par tiles."""
    base = {(x, y) for x in range(4) for y in range(4)}
    context = context_for(base)

    grow_square = {(x, 4) for x in range(5)} | {(4, y) for y in range(5)}
    square_total = evaluate_tile_set(grow_square, context)["total"]

    grow_cluster = {(x, 10) for x in range(10)}
    cluster_total = evaluate_tile_set(grow_cluster, context)["total"]

    assert evaluate_tile_set(grow_square, context)["gains"]["all_square"] == 1
    assert square_total > cluster_total


def test_staleness_stays_below_priority_resolution():
    """Bonus za stari nesmi prehodit poradi dane prioritami (min. rozestup 2)."""
    never_visited = evaluate_tile_set({(9, 9)}, context_for({(0, 0)}))
    assert never_visited["staleness"] == pytest.approx(1.0)
    assert never_visited["staleness"] < 2


def test_opportunities_are_ranked_by_score():
    tile_db = {(0, 0): {"last_visit": OLD, "first_visit": OLD, "visit_count": 1}}
    opportunities = find_tile_opportunities(
        {"all": tile_db, "year": tile_db, "recent": tile_db}, today=TODAY
    )
    assert opportunities
    scores = [item["score"] for item in opportunities]
    assert scores == sorted(scores, reverse=True)
    assert [item["rank"] for item in opportunities] == list(range(1, len(opportunities) + 1))


def test_every_priority_has_a_weight():
    assert {key for key, *_ in PRIORITIES} == set(PRIORITY_WEIGHTS)
