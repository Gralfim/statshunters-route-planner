NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _tile_xy(tile):
    if hasattr(tile, "x") and hasattr(tile, "y"):
        return (tile.x, tile.y)
    return (tile[0], tile[1])


def cluster_members(tile_set):
    """Dlazdice, ktere se do clusteru pocitaji: navstivene a se VSEMI ctyrmi
    sousedy navstivenymi (definice StatsHunters/VeloViewer).

    Drive se bral kazdy navstiveny tile a cluster byl prosta 4-souvisla
    komponenta - vyslo 467 proti 256, ktere ukazuje profil na StatsHunters
    (897 dlazdic, overeno 09/2026; tahle definice dava presne 256). Rozdil neni
    kosmeticky: s prostou komponentou pridala cluster KAZDA dlazdice na okraji,
    takze "zvetsi cluster" znamenalo jen "soused navstiveneho" a letosni cluster
    se rovnal vsem letosnim dlazdicim. Skutecny cluster roste vyplnenim der a
    druhou radou dlazdic za okrajem.
    """
    visited = {_tile_xy(tile) for tile in tile_set}
    return {
        (x, y) for x, y in visited
        if all((x + dx, y + dy) in visited for dx, dy in NEIGHBOURS)
    }


def find_largest_cluster(tile_set):
    """Max cluster: nejvetsi 4-souvisla skupina dlazdic clusteru (cluster_members)."""
    remaining = cluster_members(tile_set)
    largest = set()

    while remaining:
        start = remaining.pop()
        cluster = {start}
        stack = [start]

        while stack:
            x, y = stack.pop()
            for dx, dy in NEIGHBOURS:
                neighbour = (x + dx, y + dy)
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    cluster.add(neighbour)
                    stack.append(neighbour)

        if len(cluster) > len(largest):
            largest = cluster

    return {"size": len(largest), "tiles": sorted(largest)}
