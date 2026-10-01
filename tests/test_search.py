"""Stavba posloupnosti waypointu (routeplan): hodnota na kilometr, rezie
waypointu v odhadu, co vypustit, odkud zacit.

Vse na odhadech - bez grafu. Referencni pripad, kvuli kteremu to vzniklo:
rucni trasa Stodulky -> Barrandov (08/2026). Planovac ji nenasel, protoze
vsechny posloupnosti zacinaly u tychz dlazdic na severu, plnily se k horni
hranici okna a pretecenou trasu pak opravil vypustenim dlazdice s nejnizsim
skore - cele jizni skupiny.
"""
from datetime import date, datetime

import pytest

from geo import tile_center
from routeplan import (MAX_REGION_SEEDS, MIN_INSERT_M, REGION_TILES, WAYPOINT_OVERHEAD_M,
                       _drop_order, _estimate_gap_m, _estimate_path_m, _estimate_points,
                       _rank_fill, _ratio_fill, _region_seeds, _within_reach)
from scoring import build_route_context

HOME = (8850, 5550)
START = tile_center(HOME)


def item(tile, value):
    lat, lon = tile_center(tile)
    return {"tile": tile, "score": value, "value": value, "lat": lat, "lon": lon}


def plain_estimate(sequence):
    points = _estimate_points(START, START, sequence)
    return sum(_estimate_gap_m(a, b) for a, b in zip(points, points[1:]))


def test_every_further_waypoint_costs_its_overhead():
    """Mereno na 68 posloupnostech: skutecna delka ~ odhad + 1,4 km za kazdy
    dalsi waypoint (u jednoho waypointu odhad sedel)."""
    one = [item((8853, 5550), 1)]
    three = one + [item((8853, 5551), 1), item((8853, 5552), 1)]
    assert _estimate_path_m(START, START, one) == pytest.approx(plain_estimate(one))
    assert _estimate_path_m(START, START, three) == pytest.approx(
        plain_estimate(three) + 2 * WAYPOINT_OVERHEAD_M)


def test_ratio_fill_does_not_mix_opposite_directions():
    """Dve stejne cenne skupiny na opacnych stranach, rozpocet na jednu: drive
    vznikla posloupnost pres obe (odhad 16,6 km, skutecne 29 km)."""
    north = [item((8850, 5545), 100), item((8851, 5545), 100)]
    south = [item((8850, 5555), 100), item((8851, 5555), 100)]
    budget = _estimate_path_m(START, START, north) + 1000
    sequence = _ratio_fill(START, START, [], north + south, budget)
    rows = {candidate["tile"][1] for candidate in sequence}
    assert len(sequence) == 2
    assert rows == {5545} or rows == {5555}


def test_ratio_fill_respects_the_budget():
    pool = [item((8850 + dx, 5548), 50) for dx in range(6)]
    budget = 12000
    sequence = _ratio_fill(START, START, [], pool, budget)
    assert sequence
    assert _estimate_path_m(START, START, sequence) <= budget


def test_ratio_prefers_value_per_kilometre_rank_prefers_value():
    """Obe plneni maji v portfoliu svou roli: pomer bere blizkou dlazdici,
    poradi hodnoty tu vzdalenejsi a cennejsi."""
    near = item((8852, 5550), 30)
    far = item((8850, 5543), 100)
    budget = max(_estimate_path_m(START, START, [near]), _estimate_path_m(START, START, [far])) + 100
    assert [c["tile"] for c in _ratio_fill(START, START, [], [near, far], budget)] == [near["tile"]]
    assert [c["tile"] for c in _rank_fill(START, START, [near, far], budget)] == [far["tile"]]


def test_drop_removes_what_saves_most_per_value_not_the_lowest_score():
    """Stejna hodnota: vypadne vzdalenejsi dlazdice (usetri vic). Drive
    rozhodovalo skore a pri shode bonus za stari."""
    near = item((8852, 5550), 50)
    far = item((8850, 5543), 50)
    assert _drop_order(START, START, [near, far])[0] is far

    precious_far = item((8850, 5543), 1000)
    assert _drop_order(START, START, [near, precious_far])[0] is near


def test_region_seed_skips_a_tile_the_route_cannot_reach():
    """Nejcennejsi dlazdice oblasti byva nejvzdalenejsi; trasa k ni presahla
    okno a po oprave z ni zbyla prima cesta bez prinosu."""
    cell_x = (8850 // REGION_TILES) * REGION_TILES
    reachable = item((cell_x, 5553), 268)
    too_far = item((cell_x + 2, 5557 + 3 * REGION_TILES), 332)
    limit = _estimate_path_m(START, START, [reachable]) + 100
    seeds = _region_seeds([reachable, too_far], START, START, limit)
    assert [seed[0]["tile"] for seed in seeds] == [reachable["tile"]]


def test_one_seed_per_region_and_capped():
    pool = [item((8850 + REGION_TILES * k, 5550), 10 + k) for k in range(MAX_REGION_SEEDS + 3)]
    pool.append(item((8850 + 1, 5550), 1))  # stejna oblast jako prvni, mene cenna
    seeds = _region_seeds(pool, START, START, float("inf"))
    assert len(seeds) == MAX_REGION_SEEDS
    cells = {(s[0]["tile"][0] // REGION_TILES, s[0]["tile"][1] // REGION_TILES) for s in seeds}
    assert len(cells) == len(seeds)
    assert seeds[0][0]["value"] == max(c["value"] for c in pool)


def test_search_value_includes_progress_toward_the_next_square():
    """Dlazdice chybejici ve square okne ma skore jako kazda jina, jeji cena je
    v postupu k square - bez toho ji hledani nemelo proc zkusit."""
    missing = {(8853, 5554), (8854, 5553), (8854, 5554)}
    world = {(8850 + dx, 5550 + dy) for dx in range(5) for dy in range(5)} - missing
    visit = datetime(2020, 1, 1)
    db = {tile: {"last_visit": visit, "first_visit": visit, "visit_count": 1} for tile in world}
    context = build_route_context({"all": db, "year": db}, today=date(2026, 7, 29))

    candidates = [{"tile": tile, "score": 1.0} for tile in sorted(missing)]
    candidates.append({"tile": (8849, 5550), "score": 1.0})  # mimo okno
    within = {c["tile"]: c for c in _within_reach(candidates, START, START, 20000, context)}
    assert all(within[tile]["value"] > within[tile]["score"] for tile in missing)
    assert within[(8849, 5550)]["value"] == within[(8849, 5550)]["score"]


def test_min_insert_keeps_nearly_free_tiles_finite():
    assert MIN_INSERT_M > 0
