"""Opakovany koridor: kolik behu se odehraje na mistech, ktera uvidim dvakrat.

`repeated_m` umi jen shodu hran, takze okruh vedouci udolim tam po jedne strane
a zpet po druhe ma opakovani nulove a pritom je porad na tomtez miste. Merka se
proto pocita geometricky ze souradnic trasy.
"""
import math

from runcost import CORRIDOR_RADIUS_M, CORRIDOR_SEPARATION_M, corridor_m

LAT, LON = 50.08, 14.42


def point(north_m, east_m):
    return (LAT + north_m / 111320.0,
            LON + east_m / (111320.0 * math.cos(math.radians(LAT))))


def line(from_m, to_m, offset_m=0.0, step_m=10.0):
    """Rovny usek podel osy sever-jih, posunuty o offset_m na vychod."""
    count = int(abs(to_m - from_m) / step_m)
    direction = 1 if to_m >= from_m else -1
    return [point(from_m + direction * i * step_m, offset_m) for i in range(count + 1)]


def test_a_route_that_never_returns_has_no_repeated_corridor():
    assert corridor_m(line(0, 2000)) == 0.0


def test_running_back_the_same_way_counts_both_passes():
    """Tam a zpet touz cestou: v koridoru je cela trasa, ne jen zpatecni pulka -
    merka odpovida na "kolik behu strávim na mistech, ktera uz znam"."""
    there_and_back = line(0, 2000) + line(2000, 0)
    assert corridor_m(there_and_back) > 0.9 * 4000


def test_a_parallel_path_counts_even_though_no_edge_repeats():
    """Jadro P1-2: zpatecni cesta po soubezne pesine je JINA hrana, takze
    `repeated_m` ji nevidi vubec."""
    offset = CORRIDOR_RADIUS_M / 2
    assert corridor_m(line(0, 2000) + line(2000, 0, offset_m=offset)) > 0.9 * 4000


def test_a_genuinely_different_street_is_not_a_repeat():
    """Soubezna ulice dal nez polomer uz je jine misto - jinak by merka
    trestala kazdou trasu v pravidelne mestske siti."""
    assert corridor_m(line(0, 2000) + line(2000, 0, offset_m=3 * CORRIDOR_RADIUS_M)) == 0.0


def test_a_bend_is_not_a_repeated_corridor():
    """Bez pozadavku na odstup po trase by se hlasil kazdy ohyb: U-zatacka
    o polomeru rovnem tomu, co merka povazuje za tyz koridor, ma oblouk kratsi
    nez CORRIDOR_SEPARATION_M."""
    radius = CORRIDOR_RADIUS_M
    bend = [point(radius * math.sin(t / 20 * math.pi), radius * (1 - math.cos(t / 20 * math.pi)))
            for t in range(21)]
    assert corridor_m(line(-500, 0) + bend + line(0, -500, offset_m=2 * radius)) == 0.0


def test_a_short_out_and_back_stays_below_the_separation():
    """Kratke slepe ocasky resi _trim_spurs uz pri stavbe trasy - merka koridoru
    je na dlouhe soubehy, ne na odbocku k vyhlidce."""
    spur = CORRIDOR_SEPARATION_M / 3
    assert corridor_m(line(0, spur) + line(spur, 0)) == 0.0


def test_an_empty_route_is_not_an_error():
    assert corridor_m([]) == 0.0
    assert corridor_m([point(0, 0)]) == 0.0
