"""Vlastnosti dlazdic pro mapu (api._tile_props).

Mapa barvi letosni dlazdice podle stari posledni navstevy - te slozky ceny,
podle ktere planovac vybira okruhy z domova. Cena v mape se proto bere primo ze
scoringu: kdyby se pocitala zvlast, mohla by ukazovat neco jineho, nez s cim
planovac pocita (napr. po zmene nulove zony).
"""
from datetime import date, datetime

from api import _tile_props
from models import Tile
from scoring import STALENESS_FRESH_DAYS, _staleness_bonus

TODAY = date(2026, 10, 1)


def record(last_visit):
    return {"visit_count": 3, "first_visit": datetime(2024, 5, 1), "last_visit": last_visit}


def test_tile_carries_age_and_the_planners_price():
    props = _tile_props(Tile(8850, 5550), record(datetime(2026, 5, 4)), today=TODAY)
    assert props["days_since_visit"] == 150
    assert props["staleness"] == round(_staleness_bonus(150), 3)
    assert props["last_visit"] == "2026-05-04"


def test_freshly_run_tile_is_worth_nothing_on_the_map_too():
    day = date.fromordinal(TODAY.toordinal() - STALENESS_FRESH_DAYS)
    props = _tile_props(Tile(1, 1), record(datetime(day.year, day.month, day.day)), today=TODAY)
    assert props["days_since_visit"] == STALENESS_FRESH_DAYS
    assert props["staleness"] == 0.0


def test_legend_scale_comes_from_the_planners_price():
    """Barva letosni dlazdice = cena / cena po roce; meritko i znacky legendy
    musi sedet na tutez funkci, jinak by barva a legenda ukazovaly jinou cenu."""
    from api import _staleness_scale

    scale = _staleness_scale()
    assert scale["fresh_days"] == STALENESS_FRESH_DAYS
    assert scale["year"] == round(_staleness_bonus(365), 3)
    assert scale["ticks"][0] == {"days": STALENESS_FRESH_DAYS, "staleness": 0.0}
    prices = [tick["staleness"] for tick in scale["ticks"]]
    assert prices == sorted(prices) and prices[-1] == scale["year"]
