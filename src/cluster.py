def _tile_xy(tile):
    if hasattr(tile, "x") and hasattr(tile, "y"):
        return (tile.x, tile.y)
    return (tile[0], tile[1])


def find_largest_cluster(tile_set):
    """Find the largest 4-neighbour connected component."""
    remaining = {_tile_xy(tile) for tile in tile_set}
    largest = set()

    while remaining:
        start = remaining.pop()
        cluster = {start}
        stack = [start]

        while stack:
            x, y = stack.pop()
            for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    cluster.add(neighbour)
                    stack.append(neighbour)

        if len(cluster) > len(largest):
            largest = cluster

    return {"size": len(largest), "tiles": sorted(largest)}
