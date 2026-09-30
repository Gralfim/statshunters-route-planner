"""Bodovani prinosu: vahy priorit, spolecny prinos mnoziny, staleness."""
from datetime import date, datetime, timedelta

import pytest

from scoring import (PERIODS, PRIORITIES, PRIORITY_WEIGHTS, STALE_ONLY_PRIORITY,
                     STALENESS_FRESH_DAYS, _staleness_bonus, build_route_context,
                     evaluate_tile_set, find_tile_opportunities)

TODAY = date(2026, 7, 29)
OLD = datetime(2020, 1, 1)


def db(tiles, last_visit=OLD):
    return {tile: {"last_visit": last_visit, "first_visit": last_visit,
                   "visit_count": 1} for tile in tiles}


def context_for(all_tiles, year_tiles=None, last_visit=OLD):
    """Kontext z obou obdobi; year defaultne kopiruje all."""
    return build_route_context(
        {
            "all": db(all_tiles, last_visit),
            "year": db(all_tiles if year_tiles is None else year_tiles, last_visit),
        },
        today=TODAY,
    )


def test_priority_weights_keep_period_order():
    """Nejslabsi priorita delsiho obdobi musi prebit nejsilnejsi priorita
    kratsiho - jinak by letosni metriky prehlusily celkove."""
    assert PRIORITY_WEIGHTS["all_unvisited"] > PRIORITY_WEIGHTS["year_square"]


def test_the_three_month_period_is_gone():
    """Plovouci 3mesicni okno se nedalo cilene zlepsovat (square novym behem
    naroste, ale jinde dlazdice z okna vypadne) - opakovani hlida stari."""
    assert PERIODS == ("all", "year")
    assert {period for _key, _label, period, _kind, _weight in PRIORITIES} == set(PERIODS)


def test_priority_weights_keep_kind_order_inside_period():
    for period in PERIODS:
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
    """Rust strany square (vzacny) musi prebit rust clusteru o par tiles.

    Blok 4x4 ma cluster 2x2. Dva sloupce vedle nej cluster zvetsi o 4 (blok 6x4
    ma cluster 4x2), square ale necha na 4. L-ko kolem rohu udela square 5x5."""
    base = {(x, y) for x in range(4) for y in range(4)}
    context = context_for(base)

    grow_square = {(x, 4) for x in range(5)} | {(4, y) for y in range(5)}
    square = evaluate_tile_set(grow_square, context)

    grow_cluster = {(x, y) for x in (4, 5) for y in range(4)}
    cluster = evaluate_tile_set(grow_cluster, context)

    assert square["gains"]["all_square"] == 1
    assert cluster["gains"]["all_cluster"] == 4 and cluster["gains"]["all_square"] == 0
    assert square["total"] > cluster["total"]


def test_filling_a_hole_grows_the_cluster_by_more_than_one():
    """Dira ubira clusteru i ctyri sousedy - jedina dlazdice tu da +8, zatimco
    dlazdice na okraji uzemi cluster nezvetsi vubec (s drivejsi definici to bylo
    naopak: kazdy soused navstiveneho uzemi +1)."""
    around_hole = {(x, y) for x in range(5) for y in range(5)} - {(2, 2)}
    context = context_for(around_hole)
    assert evaluate_tile_set({(2, 2)}, context)["gains"]["all_cluster"] == 8
    assert evaluate_tile_set({(5, 2)}, context)["gains"]["all_cluster"] == 0


def test_staleness_stays_below_priority_resolution():
    """Bonus za stari nesmi prehodit poradi dane prioritami: ani plny bonus
    nevyvazi jedinou letos novou dlazdici."""
    never_visited = evaluate_tile_set({(9, 9)}, context_for({(0, 0)}))
    assert never_visited["staleness"] == pytest.approx(1.0)
    assert never_visited["staleness"] < min(PRIORITY_WEIGHTS.values())


def test_staleness_grows_with_age_and_saturates():
    ages = [0, 1, 7, 30, 90, 365, 3 * 365, 10 * 365]
    bonuses = [_staleness_bonus(days) for days in ages]
    assert bonuses == sorted(bonuses)
    assert bonuses[0] == 0.0
    assert bonuses[-2] == pytest.approx(1.0) and bonuses[-1] == pytest.approx(1.0)
    assert _staleness_bonus(None) == 1.0


def test_freshly_run_tiles_are_worth_nothing():
    """Nulova zona: kratky okruh nema duvod vest pres to, co se nedavno probehlo.
    Cisty logaritmus daval 3 tydny stare dlazdici pres pul ctvrtletni hodnoty
    a okruhy se tim opakovaly (mereno: 6 z 11 dlazdic z poslednich 3 tydnu)."""
    assert _staleness_bonus(1) == 0.0
    assert _staleness_bonus(STALENESS_FRESH_DAYS) == 0.0
    assert _staleness_bonus(STALENESS_FRESH_DAYS + 1) > 0.0


def test_staleness_is_logarithmic_after_the_fresh_zone():
    """Rozdil mezi rokem a tremi lety uz tolik neznamena (linearne trojnasobek)."""
    year, three_years = _staleness_bonus(365), _staleness_bonus(3 * 365)
    assert three_years / year < 1.5
    quarter, half = _staleness_bonus(91), _staleness_bonus(182)
    assert half - quarter > three_years - _staleness_bonus(2 * 365)


def test_route_over_old_tiles_beats_route_over_last_weeks_tiles():
    """Stejny pocet dlazdic bez jakekoli priority: rozhodne stari."""
    tiles = {(x, 0) for x in range(6)}
    recent = datetime(2026, 7, 29) - timedelta(days=STALENESS_FRESH_DAYS)
    spring = datetime(2026, 4, 1)
    fresh = build_route_context({"all": db(tiles, recent), "year": db(tiles, recent)},
                                today=TODAY)
    older = build_route_context({"all": db(tiles, spring), "year": db(tiles, spring)},
                                today=TODAY)
    assert evaluate_tile_set(tiles, older)["total"] > 0
    assert evaluate_tile_set(tiles, fresh)["total"] == 0


def test_visited_tiles_without_priority_are_stale_only_candidates():
    """V dosahu domova je vse letos navstivene - bez techto kandidatu by okruh
    nemel podle ceho planovat. Dnes probehnuta dlazdice cenu nema."""
    tile_db = {
        (0, 0): {"last_visit": datetime(2026, 5, 1), "first_visit": OLD, "visit_count": 3},
        (1, 0): {"last_visit": datetime(2026, 7, 29), "first_visit": OLD, "visit_count": 1},
    }
    opportunities = {
        tuple(item["tile"]): item
        for item in find_tile_opportunities({"all": tile_db, "year": tile_db}, today=TODAY)
    }
    candidate = opportunities[(0, 0)]
    assert candidate["stale_only"] and not candidate["reasons"]
    assert candidate["priority"] == STALE_ONLY_PRIORITY
    assert candidate["score"] == pytest.approx(_staleness_bonus(89), abs=1e-3)
    assert (1, 0) not in opportunities  # cerstva - v nulove zone
    frontier = opportunities[(2, 0)]
    assert not frontier["stale_only"] and frontier["reasons"]


def test_opportunities_are_ranked_by_score():
    tile_db = {(0, 0): {"last_visit": OLD, "first_visit": OLD, "visit_count": 1}}
    opportunities = find_tile_opportunities({"all": tile_db, "year": tile_db}, today=TODAY)
    assert opportunities
    scores = [item["score"] for item in opportunities]
    assert scores == sorted(scores, reverse=True)
    assert [item["rank"] for item in opportunities] == list(range(1, len(opportunities) + 1))


def test_every_priority_has_a_weight():
    assert {key for key, *_ in PRIORITIES} == set(PRIORITY_WEIGHTS)
