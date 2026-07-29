"""Metriky nad mnozinou tiles: max square, max cluster."""
from cluster import find_largest_cluster
from square import find_largest_square


def test_square_of_empty_set():
    assert find_largest_square([])["size"] == 0


def test_square_finds_full_block():
    tiles = {(x, y) for x in range(3) for y in range(3)}
    assert find_largest_square(tiles)["size"] == 3


def test_square_ignores_hole():
    tiles = {(x, y) for x in range(3) for y in range(3)} - {(1, 1)}
    assert find_largest_square(tiles)["size"] == 1


def test_square_returns_its_tiles():
    tiles = {(x, y) for x in range(2) for y in range(2)} | {(9, 9)}
    result = find_largest_square(tiles)
    assert result["size"] == 2
    assert set(result["tiles"]) == {(x, y) for x in range(2) for y in range(2)}


def test_cluster_of_empty_set():
    assert find_largest_cluster([])["size"] == 0


def test_cluster_takes_largest_component():
    tiles = {(0, 0), (1, 0), (2, 0)} | {(10, 10), (10, 11)}
    assert find_largest_cluster(tiles)["size"] == 3


def test_cluster_is_four_connected_not_diagonal():
    """Diagonalni soused nestaci - jinak by se cluster pocital jinak nez na
    StatsHunters."""
    assert find_largest_cluster({(0, 0), (1, 1)})["size"] == 1
