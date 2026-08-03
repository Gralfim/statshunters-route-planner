"""Orez slepych ocasku nesmi ubrat hloubku pruniku do dlazdice.

Ocasek do dlazdice je casto prave to, kvuli cemu se do ni zajizdelo. Puvodni
orez hlidal jen to, aby dlazdici pokryval JAKYKOLI jiny uzel trasy - a nechal
tak trasu, ktera ji jen skrabne. Mereno na referencni trase: cilova dlazdice
mela v bezpecne zone 2 362 uzlu (az 778 m hluboko), ale trasa ji prosla
nejhloub 49 m, pod TILE_MARGIN_M chranicim proti chybe GPS.
"""
import pytest

from geojson import lon_lat_tile, tile_lon_lat
from routeplan import TILE_MARGIN_M, _trim_spurs

TILE = lon_lat_tile(14.42, 50.075)
WEST, NORTH = tile_lon_lat(TILE[0], TILE[1])
EAST, SOUTH = tile_lon_lat(TILE[0] + 1, TILE[1] + 1)
MIDDLE_LON = (WEST + EAST) / 2


def inside(depth_m):
    """Bod v TILE vzdaleny depth_m od nejblizsi (jizni) hranice."""
    return (SOUTH + depth_m / 111320.0, MIDDLE_LON)


def outside(offset_m=50.0):
    return (SOUTH - offset_m / 111320.0, MIDDLE_LON)


@pytest.fixture
def spur(line_graph):
    """Trasa s ocaskem: z venku do dlazdice, k vnitrnimu bodu a stejnou cestou
    zpet. `edges` staci jako kostra - orez pracuje jen se souradnicemi uzlu."""
    def build(base_depth, tip_depth):
        nodes = {"out": outside(), "base": inside(base_depth), "tip": inside(tip_depth),
                 "end": outside(120.0)}
        graph = line_graph(nodes, [("out", "base", {}), ("base", "tip", {}),
                                   ("base", "end", {})])
        other = lon_lat_tile(nodes["out"][1], nodes["out"][0])
        node_tiles = {"out": other, "base": TILE, "tip": TILE, "end": other}
        return graph, node_tiles, ["out", "base", "tip", "base", "end"]
    return build


def test_deep_tip_survives_the_trim(spur):
    """Spicka je jediny bod dost hluboko - bez ni by dlazdice zustala jen
    skrabnuta."""
    graph, node_tiles, path = spur(base_depth=30.0, tip_depth=200.0)
    assert "tip" in _trim_spurs(graph, node_tiles, path)


def test_shallow_tip_is_trimmed(spur):
    """Kdyz ocasek hloubku nepridava, je to jen hluchá vzdalenost."""
    graph, node_tiles, path = spur(base_depth=30.0, tip_depth=40.0)
    assert "tip" not in _trim_spurs(graph, node_tiles, path)


def test_tip_is_trimmed_when_the_base_is_deep_enough(spur):
    """Dva dost hluboke body - jeden smi odpadnout."""
    graph, node_tiles, path = spur(base_depth=TILE_MARGIN_M + 60, tip_depth=TILE_MARGIN_M + 90)
    assert "tip" not in _trim_spurs(graph, node_tiles, path)


def test_trim_keeps_the_route_connected(spur):
    graph, node_tiles, path = spur(base_depth=30.0, tip_depth=40.0)
    trimmed = _trim_spurs(graph, node_tiles, path)
    assert trimmed[0] == "out" and trimmed[-1] == "end"
    assert all(a != b for a, b in zip(trimmed, trimmed[1:]))


def test_route_without_spurs_is_untouched(spur):
    graph, node_tiles, _path = spur(base_depth=100.0, tip_depth=200.0)
    straight = ["out", "base", "tip"]
    assert _trim_spurs(graph, node_tiles, straight) == straight
