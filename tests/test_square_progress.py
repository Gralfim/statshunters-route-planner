"""Strategicky postup k budoucimu square.

Dlazdice, ktera max square jeste nezvetsi, ale priblizi ho, ma cenu - prave
kvuli tomu se nekdy bezi. `evaluate_tile_set` ji ohodnoti nulou, protoze meri
jen to, co se zlepsi TED; takova trasa pak vypadala hure nez trasa bez jakekoli
navaznosti. Overeno na skutecnem behu uzivatele (08/2026): doplnil 1 z 5
dlazdic chybejicich k rocnimu square 12x12 a soucasna cisla to nepopsala.
"""
from datetime import date, datetime

from scoring import (PRIORITY_WEIGHTS, SQUARE_PROGRESS_FRACTION, build_route_context,
                     evaluate_tile_set, square_progress)

TODAY = date(2026, 7, 29)
VISIT = datetime(2020, 1, 1)


MISSING = {(3, 4), (4, 3), (4, 4)}


def world():
    """Blok 5x5 bez trech dlazdic v rohu: nejvetsi square ma stranu 4 a k petce
    chybi prave ty tri. Okna se hledaji jen uvnitr obalky navstivenych dlazdic,
    takze samotny plny ctverec 4x4 by zadne okno nenabidl."""
    return {(x, y) for x in range(5) for y in range(5)} - MISSING


def context_for(tiles):
    """Kontext jen s obdobim "all" - year a recent zustavaji prazdne, aby se
    postup dal cist z jednoho okna."""
    def db(items):
        return {tile: {"last_visit": VISIT, "first_visit": VISIT, "visit_count": 1}
                for tile in items}

    return build_route_context({"all": db(tiles), "year": {}, "recent": {}}, today=TODAY)


def test_a_route_that_fills_nothing_has_no_progress():
    context = context_for(world())
    assert square_progress({(50, 50)}, context) == 0.0


def test_filling_part_of_the_next_square_counts():
    """Ctverec 4x4 chce pro stranu 5 doplnit radu i sloupec; kdo prinese kus,
    udelal kus prace."""
    context = context_for(world())
    partial = square_progress({(4, 3)}, context)
    assert partial > 0
    assert evaluate_tile_set({(4, 3)}, context)["gains"]["all_square"] == 0


def test_more_of_the_same_window_is_worth_more():
    context = context_for(world())
    assert (square_progress({(4, 3), (4, 4)}, context)
            > square_progress({(4, 3)}, context))


def test_a_completed_window_is_not_counted_as_progress():
    """Dokoncene okno uz je skutecny zisk a je v evaluate_tile_set - zapocitat
    ho i sem by tentyz krok platilo dvakrat."""
    context = context_for(world())
    assert evaluate_tile_set(MISSING, context)["gains"]["all_square"] == 1
    assert square_progress(MISSING, context) == 0.0


def test_progress_never_outweighs_actually_finishing_it():
    """Postup se kráti (SQUARE_PROGRESS_FRACTION), aby "skoro square" neprebilo
    square - jinak by planovac radeji navzdy zacinal, nez jednou dokoncil."""
    context = context_for(world())
    almost = square_progress(MISSING - {(4, 4)}, context)
    done = evaluate_tile_set(MISSING, context)["total"]
    assert almost < done


def test_the_fraction_is_the_share_of_the_window_filled():
    context = context_for(world())
    windows = context["square_windows"]["all"]
    value, candidates = windows
    smallest = min(candidates, key=len)
    assert len(smallest) == len(MISSING)
    assert square_progress({next(iter(smallest))}, context) == round(
        SQUARE_PROGRESS_FRACTION * value / len(smallest), 3)


def test_window_value_follows_the_square_priority_weight():
    context = context_for(world())
    value, _windows = context["square_windows"]["all"]
    # strana 5 misto 4 = 25 - 16 = 9 policek plochy
    assert value == PRIORITY_WEIGHTS["all_square"] * 9
