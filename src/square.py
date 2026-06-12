def _tile_xy(tile):
    if hasattr(tile, "x") and hasattr(tile, "y"):
        return (tile.x, tile.y)
    return (tile[0], tile[1])


def find_largest_square(tile_set):
    """Find the largest axis-aligned square fully covered by visited tiles."""
    occupied = {_tile_xy(tile) for tile in tile_set}
    if not occupied:
        return {"size": 0, "tiles": [], "origin": None}

    xs = sorted({x for x, _ in occupied})
    ys = sorted({y for _, y in occupied})
    best_size = 0
    best_origin = None
    dp = {}

    for x in reversed(xs):
        for y in reversed(ys):
            if (x, y) not in occupied:
                dp[(x, y)] = 0
                continue

            dp[(x, y)] = 1 + min(
                dp.get((x + 1, y), 0),
                dp.get((x, y + 1), 0),
                dp.get((x + 1, y + 1), 0),
            )
            if dp[(x, y)] > best_size:
                best_size = dp[(x, y)]
                best_origin = (x, y)

    tiles = []
    if best_origin is not None:
        ox, oy = best_origin
        tiles = [
            (x, y)
            for x in range(ox, ox + best_size)
            for y in range(oy, oy + best_size)
        ]

    return {"size": best_size, "origin": best_origin, "tiles": tiles}
