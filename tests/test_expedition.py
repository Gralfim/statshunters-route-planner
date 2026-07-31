"""Vypravy s MHD: casove okno behu a vyber cilu pro MHD."""
from datetime import date, datetime

import pytest

from expedition import (MIN_TRANSIT_TARGET_SHARE, ONEWAY_HOME_SHARE, _harvest_estimate,
                        _loop_window, _opportunity_points)
from scoring import build_route_context

PACE = 6.0
BUDGET = 120.0


def window(target=15.0, tolerance=3.0, walks=0.0, budget=BUDGET, transit=0.0, pace=PACE):
    return _loop_window(target, tolerance, walks, budget, transit, pace)


# --- casove okno behu ---

def test_pure_loop_gets_the_whole_requested_window():
    """Bez MHD a s dost velkym rozpoctem (120 min pri 6 min/km = 20 km) se okno
    rovna zadane tolerance."""
    center, spread = window()
    assert (center - spread, center + spread) == pytest.approx((12.0, 18.0))


def test_tight_budget_caps_the_window_below_the_tolerance():
    """Kdyz na beh neni cas, strop dava rozpocet - ne tolerance."""
    center, spread = window(budget=90.0)
    assert center + spread == pytest.approx(15.0)  # 90 min / 6 min/km
    assert center - spread == pytest.approx(12.0)


def test_walking_to_the_stop_counts_against_the_run():
    """Dobeh na zastavku a zpet je soucast behu, okruh se o nej zkrati."""
    center, spread = window(walks=3.0, transit=30.0)
    assert center - spread == pytest.approx(9.0)


def test_transit_time_shrinks_the_run():
    short = window(transit=20.0)
    long = window(transit=40.0)
    assert long[0] + long[1] < short[0] + short[1]


def test_expedition_that_cannot_fit_gives_nothing():
    assert window(transit=120.0) is None


def test_transit_ceiling_comes_from_the_distance_requirement():
    """Beh musi splnit 12-18 km, coz pri tempu 6 min/km sezere 72 minut - na MHD
    pak zbyva nejvys 24 minut na jednu cestu. Odtud padaji vzdalene cile."""
    assert window(transit=2 * 24.0) is not None
    assert window(transit=2 * 25.0) is None


def test_short_budget_leaves_no_room_at_all():
    assert window(budget=60.0) is None


# --- jednosmerna vyprava (MHD tam, beh domu) ---

def test_one_way_gets_a_wider_run_than_the_round_trip():
    """Jadro jednosmerneho tvaru: usetrena zpatecni jizda i dobeh ze zastavky
    domu se prelije do behu. Mereno z Karlova nam.: okruh se zpatecni jizdou dal
    10,8 km behu a prinos 125, jednosmerna varianta 15,2 km a prinos 308."""
    round_trip = window(walks=2 * 1.3, transit=2 * 19.0)
    one_way = window(walks=1.3, transit=19.0)
    assert one_way[0] + one_way[1] > round_trip[0] + round_trip[1]


def test_one_way_survives_a_connection_the_round_trip_cannot_afford():
    """Spojeni delsi nez 24 minut zabije okruh, ale jednosmernou vypravu ne -
    plati se jen jednou."""
    assert window(transit=2 * 30.0) is None
    assert window(transit=30.0) is not None


def test_home_must_be_within_reach_of_the_run():
    """Jednosmerny tvar ma smysl, jen kdyz se domu da dobehnout. Podil (ne cela
    delka), aby trase zbyla rezerva na zajizdky za dlazdicemi."""
    assert 0 < ONEWAY_HOME_SHARE < 1
    run_max_km = 18.0
    assert run_max_km * ONEWAY_HOME_SHARE < run_max_km


# --- predfiltr cilu pro MHD ---

def test_nearby_areas_are_not_excluded_from_transit():
    """Regrese: driv se vyrazovalo vsechno blizsi nez 8,1 km s oduvodnenim
    "tam si dobehnes sam". Strasnicka (5,5 km) ani Skalka (6,3 km) se pak nikdy
    neuvazovaly, prestoze metrem A jsou za 9-16 minut."""
    threshold_km = 15.0 * MIN_TRANSIT_TARGET_SHARE
    assert threshold_km < 5.5, f"prah {threshold_km} km by vyradil Strasnicku"
    assert threshold_km < 6.3, f"prah {threshold_km} km by vyradil Skalku"


def test_very_close_areas_stay_excluded():
    """Na zastavku se taky musi dojit - u cile za rohem se to nevyplati."""
    assert 15.0 * MIN_TRANSIT_TARGET_SHARE > 2.0


# --- odhad sklizne ---

def context_with(tiles, visited=()):
    def db(items):
        moment = datetime(2020, 1, 1)
        return {tile: {"last_visit": moment, "first_visit": moment, "visit_count": 1}
                for tile in items}

    return build_route_context({"all": db(visited), "year": db(visited), "recent": db(visited)},
                               today=date(2026, 7, 31))


def opportunities_at(tiles):
    return [{"tile": tile, "score": score} for tile, score in tiles]


def test_harvest_sees_only_what_is_within_reach():
    """Dlazdice za dosahem okruhu se do odhadu nesmi pocitat."""
    context = context_with([], visited=[(8850, 5550)])
    near = _opportunity_points(opportunities_at([((8851, 5550), 100.0)]))
    far = _opportunity_points(opportunities_at([((8900, 5550), 100.0)]))
    lat, lon = near[0][0], near[0][1]
    assert _harvest_estimate(lat, lon, 10.0, near, context) > 0
    assert _harvest_estimate(lat, lon, 10.0, far, context) == 0.0


def test_harvest_is_capped_by_what_a_loop_can_cross():
    """Delsi okruh smi pobrat vic dlazdic - jinak by odhad nerozlisil okruh
    5 km od okruhu 20 km."""
    context = context_with([], visited=[(8850, 5550)])
    tiles = [((8850 + dx, 5550), 10.0) for dx in range(1, 12)]
    points = _opportunity_points(opportunities_at(tiles))
    lat, lon = points[0][0], points[0][1]
    assert _harvest_estimate(lat, lon, 20.0, points, context) >= \
        _harvest_estimate(lat, lon, 5.0, points, context)


def test_harvest_of_empty_surroundings_is_zero():
    assert _harvest_estimate(50.0, 14.0, 10.0, [], context_with([])) == 0.0
