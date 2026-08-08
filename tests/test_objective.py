"""Cilova funkce trasy: prinos snizeny merkami kvality.

Vzorec je jadro vyberu trasy - rozhoduje, kterou z porovnavanych variant
planovac vezme. Testuje se na rucne sestavenych "details" slovnicich, protoze
o skutecny graf tu nejde.
"""
import pytest

from routeplan import (CORRIDOR_PENALTY_FRACTION, LENGTH_PENALTY_FRACTION, MAX_VARIANTS,
                       TRAIL_PENALTY_FRACTION, _distinct_variants, _variant_score)

TARGET_M = 15000.0
TOLERANCE_M = 3000.0


def details(benefit=100.0, length_m=TARGET_M, corridor_m=0.0, along_major_m=0.0,
            trail_m=None):
    """Vychozi trasa je "bezvadna": na cili, bez opakovani, mimo vyznamne ulice
    a cela po znacenych trasach - takze si drzi cely prinos a jednotlive merky
    jde testovat izolovane."""
    return {
        "benefit": {"total": benefit},
        "length_m": length_m,
        "corridor_m": corridor_m,
        "along_major_m": along_major_m,
        "trail_m": length_m if trail_m is None else trail_m,
    }


def score(quiet_weight=0.6, **kwargs):
    return _variant_score(details(**kwargs), TARGET_M, TOLERANCE_M, quiet_weight)


def test_clean_route_at_target_keeps_its_whole_benefit():
    assert score() == pytest.approx(100.0)


def test_score_never_exceeds_the_benefit():
    """Merky prinos jen SNIZUJI - zadna kombinace nesmi trasu prehodnotit nad
    jeji skutecny zisk pro statistiky."""
    for kwargs in ({}, dict(length_m=TARGET_M + 500), dict(corridor_m=2000),
                   dict(along_major_m=3000), dict(along_major_m=TARGET_M, corridor_m=TARGET_M)):
        assert score(**kwargs) <= 100.0 + 1e-9


def test_score_never_goes_negative():
    """Nejhorsi mozna trasa pri maximalni vaze klidu."""
    worst = _variant_score(
        details(benefit=200.0, length_m=TARGET_M + TOLERANCE_M,
                corridor_m=TARGET_M + TOLERANCE_M, along_major_m=TARGET_M + TOLERANCE_M),
        TARGET_M, TOLERANCE_M, 1.0,
    )
    assert worst == 0.0


def test_moderate_benefit_advantage_still_wins_at_the_default_weight():
    """Nastroj je na sber dlazdic: pri vychozim nastaveni nesmi mirne horsi
    vedeni trasy prebit vyrazne vyssi prinos."""
    better_tiles = score(benefit=150.0, along_major_m=0.25 * TARGET_M)
    nicer_route = score(benefit=100.0, along_major_m=0.0)
    assert better_tiles > nicer_route


def test_zero_benefit_scores_zero_whatever_the_quality():
    assert score(benefit=0.0) == 0.0


def test_degenerate_length_does_not_explode():
    assert _variant_score(details(length_m=0.0), TARGET_M, TOLERANCE_M, 0.6) == 0.0


# --- odchylka delky od cile ---

def test_route_at_target_beats_a_longer_one_with_the_same_benefit():
    """Jadro problemu: delsi trasa protne vic dlazdic, takze vitezily trasy
    u horni hranice tolerance. Pri stejnem prinosu musi vyhrat ta na cili."""
    assert score(length_m=TARGET_M) > score(length_m=TARGET_M + TOLERANCE_M)


def test_length_penalty_is_symmetric():
    assert score(length_m=TARGET_M - 1500) == pytest.approx(score(length_m=TARGET_M + 1500))


def test_length_penalty_at_the_window_edge_is_the_full_fraction():
    edge = score(length_m=TARGET_M + TOLERANCE_M)
    assert edge == pytest.approx(100.0 * (1 - LENGTH_PENALTY_FRACTION))


def test_length_penalty_does_not_grow_beyond_the_window():
    """Za hranici okna uz penalizace neroste - trasy mimo okno resi
    variant_key zvlast (in_window je prvni slozka klice)."""
    assert score(length_m=TARGET_M + 5 * TOLERANCE_M) == pytest.approx(
        score(length_m=TARGET_M + TOLERANCE_M)
    )


def test_longer_route_still_wins_if_it_earns_it():
    """Tolerance zustava pouzitelna: delsi trasa smi vyhrat, kdyz svou delku
    vyplati vyssim prinosem."""
    at_target = score(benefit=100.0, length_m=TARGET_M)
    longer = score(benefit=160.0, length_m=TARGET_M + TOLERANCE_M)
    assert longer > at_target


# --- podil delky podel vyznamnych ulic ---

def test_quiet_route_beats_a_noisy_one_with_the_same_benefit():
    quiet = score(along_major_m=0.0)
    noisy = score(along_major_m=0.5 * TARGET_M)
    assert quiet > noisy


def test_quiet_weight_zero_ignores_busy_streets():
    """Posuvnik na 'jen sber dlazdic' - trasa podel magistral neni penalizovana."""
    assert score(quiet_weight=0.0, along_major_m=TARGET_M) == pytest.approx(100.0)


def test_higher_quiet_weight_punishes_busy_streets_harder():
    noisy = dict(along_major_m=0.6 * TARGET_M)
    assert score(quiet_weight=1.0, **noisy) < score(quiet_weight=0.5, **noisy)


def test_quiet_weight_one_zeroes_a_route_entirely_on_busy_streets():
    assert score(quiet_weight=1.0, along_major_m=TARGET_M) == pytest.approx(0.0)


def test_quiet_weight_can_flip_the_winner():
    """Smysl P1-1: pri dostatecne vaze klidu prohraje 'hodne dlazdic po
    magistrale' s 'min dlazdic po klidu'. Pri nulove vaze naopak vyhraje."""
    noisy = dict(benefit=140.0, along_major_m=0.7 * TARGET_M)
    calm = dict(benefit=100.0, along_major_m=0.05 * TARGET_M)
    assert score(quiet_weight=0.0, **noisy) > score(quiet_weight=0.0, **calm)
    assert score(quiet_weight=1.0, **noisy) < score(quiet_weight=1.0, **calm)


# --- podil delky po znacenych trasach ---

def test_marked_trails_are_rewarded():
    """Druha strana teze preference: 'nevede podel magistraly' je jen
    nepritomnost spatneho, znacka vede udolim nebo parkem."""
    assert score(trail_m=0.7 * TARGET_M) > score(trail_m=0.3 * TARGET_M)


def test_route_off_trails_loses_the_whole_fraction():
    assert score(quiet_weight=1.0, trail_m=0.0) == pytest.approx(
        100.0 * (1 - TRAIL_PENALTY_FRACTION)
    )


def test_quiet_weight_zero_ignores_trails():
    assert score(quiet_weight=0.0, trail_m=0.0) == pytest.approx(100.0)


def test_missing_trail_measure_does_not_crash():
    """Starsi details bez trail_m (napr. z cache nebo testu) se nesmi rozbit."""
    without = {"benefit": {"total": 100.0}, "length_m": TARGET_M,
               "repeated_m": 0.0, "along_major_m": 0.0}
    assert _variant_score(without, TARGET_M, TOLERANCE_M, 0.0) == pytest.approx(100.0)


def test_trails_can_flip_the_winner():
    """Duvod, proc clen vznikl: v portfoliu lezela trasa se 72 % delky po
    znackach a prohravala s trasou se 47 %, protoze ji nic neodmenovalo."""
    plain = dict(benefit=115.0, trail_m=0.47 * TARGET_M)
    scenic = dict(benefit=100.0, trail_m=0.72 * TARGET_M)
    assert score(quiet_weight=0.0, **plain) > score(quiet_weight=0.0, **scenic)
    assert score(quiet_weight=1.0, **plain) < score(quiet_weight=1.0, **scenic)


# --- opakovany koridor ---

def test_running_the_same_corridor_twice_is_penalised():
    assert score(corridor_m=0.3 * TARGET_M) < score(corridor_m=0.0)


def test_route_entirely_in_a_repeated_corridor_loses_the_whole_fraction():
    assert score(corridor_m=TARGET_M) == pytest.approx(100.0 * (1 - CORRIDOR_PENALTY_FRACTION))


def test_missing_corridor_measure_does_not_crash():
    """Starsi details bez corridor_m (z cache nebo z testu) se nesmi rozbit."""
    plain = {"benefit": {"total": 100.0}, "length_m": TARGET_M,
             "along_major_m": 0.0, "trail_m": TARGET_M}
    assert _variant_score(plain, TARGET_M, TOLERANCE_M, 0.6) == pytest.approx(100.0)


# --- nabidka variant k vyberu ---

def variant(node_path, rank):
    return {"node_path": node_path, "rank": rank}


def pick(variants, limit=MAX_VARIANTS):
    return _distinct_variants(variants, lambda details: details["rank"], limit)


def test_winner_is_offered_first():
    winner = variant([1, 2, 3, 4], rank=10)
    other = variant([5, 6, 7, 8], rank=5)
    assert pick([other, winner])[0] is winner


def test_near_identical_variants_collapse_into_one():
    """Portfolio obsahuje hodne prepoctu TEZE sekvence (vyhybani opakovani,
    klidne varianty) - nabizet trikrat tutez trasu nema smysl."""
    original = variant([1, 2, 3, 4, 5, 6], rank=10)
    almost_same = variant([1, 2, 3, 4, 5, 9], rank=9)
    assert pick([original, almost_same]) == [original]


def test_genuinely_different_variants_are_offered():
    first = variant([1, 2, 3, 4, 5, 6], rank=10)
    elsewhere = variant([20, 21, 22, 23, 24, 25], rank=9)
    assert pick([first, elsewhere]) == [first, elsewhere]


def test_offer_is_capped():
    variants = [variant([10 * i, 10 * i + 1, 10 * i + 2], rank=-i) for i in range(8)]
    assert len(pick(variants)) == MAX_VARIANTS
    assert len(pick(variants, limit=2)) == 2


def test_route_without_edges_is_not_offered():
    assert pick([variant([7], rank=10)]) == []


def test_penalties_compose_multiplicatively():
    """Merky se nasobi, ne odcitaji - jinak by sesla dohromady zaporne skore."""
    both = score(corridor_m=TARGET_M, along_major_m=TARGET_M, quiet_weight=0.5)
    assert both == pytest.approx(100.0 * (1 - CORRIDOR_PENALTY_FRACTION) * (1 - 0.5))
    assert both > 0
