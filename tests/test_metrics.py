"""Metriky nad mnozinou tiles: max square, max cluster."""
from cluster import cluster_members, find_largest_cluster
from square import find_largest_square


def block(x0, y0, width, height):
    return {(x, y) for x in range(x0, x0 + width) for y in range(y0, y0 + height)}


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


def test_only_tiles_surrounded_on_all_four_sides_count():
    """Definice StatsHunters: z bloku 3x3 patri do clusteru jen stred. Drive se
    pocital cely blok (9) a na skutecnych datech vyslo 467 misto 256."""
    assert cluster_members(block(0, 0, 3, 3)) == {(1, 1)}
    assert find_largest_cluster(block(0, 0, 3, 3))["size"] == 1
    assert find_largest_cluster(block(0, 0, 5, 4))["size"] == 3 * 2


def test_a_line_of_tiles_has_no_cluster():
    """Souvisla rada dlazdic nema zadnou se vsemi sousedy - dlouhy beh po okraji
    uzemi cluster nezvetsi, i kdyz prida hodne dlazdic."""
    assert find_largest_cluster({(x, 0) for x in range(10)})["size"] == 0


def test_cluster_takes_largest_component():
    tiles = block(0, 0, 4, 4) | block(20, 20, 5, 5)
    result = find_largest_cluster(tiles)
    assert result["size"] == 9
    assert set(result["tiles"]) == block(21, 21, 3, 3)


def test_cluster_is_four_connected_not_diagonal():
    """Diagonalni soused nestaci - jinak by se cluster pocital jinak nez na
    StatsHunters. Dva krize: jejich stredy jsou v clusteru, dotykaji se rohem."""
    def plus(x, y):
        return {(x, y), (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)}

    tiles = plus(1, 1) | plus(2, 2)
    assert cluster_members(tiles) == {(1, 1), (2, 2)}
    assert find_largest_cluster(tiles)["size"] == 1


def test_a_hole_splits_the_cluster_and_filling_it_joins_it():
    """Dira ubere clusteru sebe i ctyri sousedy - jeji vyplneni proto zvetsi
    cluster o vic nez jednu dlazdici (tady 1 -> 9). Kvuli tomu se prinos musi
    pocitat nad celou mnozinou, ne po jednotlivych dlazdicich."""
    with_hole = block(0, 0, 5, 5) - {(2, 2)}
    assert find_largest_cluster(with_hole)["size"] == 1
    assert find_largest_cluster(with_hole | {(2, 2)})["size"] == 9
