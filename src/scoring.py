from cluster import find_largest_cluster
from geojson import tile_xy
from square import find_largest_square


PRIORITIES = [
    ("all_square", "Zvetsi celkovy max square", "all", "square"),
    ("all_cluster", "Zvetsi celkovy max cluster", "all", "cluster"),
    ("all_unvisited", "Celkove nenavstiveny tile", "all", "unvisited"),
    ("year_square", "Zvetsi letosni max square", "year", "square"),
    ("year_cluster", "Zvetsi letosni max cluster", "year", "cluster"),
    ("year_unvisited", "Letos nenavstiveny tile", "year", "unvisited"),
    ("recent_square", "Zvetsi 3mesicni max square", "recent", "square"),
    ("recent_cluster", "Zvetsi 3mesicni max cluster", "recent", "cluster"),
    ("recent_unvisited", "Za posledni 3 mesice nenavstiveny tile", "recent", "unvisited"),
]


def _tile_set(tile_db):
    return {tile_xy(tile) for tile in tile_db}


def _period_baseline(tiles):
    return {
        "tiles": tiles,
        "cluster_size": find_largest_cluster(tiles)["size"],
        "square_size": find_largest_square(tiles)["size"],
    }


def _frontier_tiles(tiles):
    frontier = set()
    for x, y in tiles:
        for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if neighbour not in tiles:
                frontier.add(neighbour)
    return frontier


def _candidate_tiles(period_tiles):
    all_tiles = period_tiles["all"]
    year_tiles = period_tiles["year"]
    recent_tiles = period_tiles["recent"]

    candidates = set()
    for tiles in period_tiles.values():
        candidates.update(_frontier_tiles(tiles))

    candidates.update(all_tiles - year_tiles)
    candidates.update(all_tiles - recent_tiles)
    return candidates


def _visit_status(tile, period_tiles):
    visited_periods = {
        period: tile in tiles
        for period, tiles in period_tiles.items()
    }
    missing_periods = [
        period
        for period in ("all", "year", "recent")
        if not visited_periods[period]
    ]
    return visited_periods, missing_periods


def _measure_gain(tile, baseline, metric):
    if tile in baseline["tiles"]:
        return 0

    expanded = baseline["tiles"] | {tile}
    if metric == "cluster":
        return find_largest_cluster(expanded)["size"] - baseline["cluster_size"]
    if metric == "square":
        return find_largest_square(expanded)["size"] - baseline["square_size"]

    raise ValueError(f"Unknown metric: {metric}")


def find_tile_opportunities(period_tile_dbs):
    period_tiles = {
        period: _tile_set(tile_db)
        for period, tile_db in period_tile_dbs.items()
    }
    baselines = {
        period: _period_baseline(tiles)
        for period, tiles in period_tiles.items()
    }

    opportunities = []
    for tile in sorted(_candidate_tiles(period_tiles)):
        reasons = []
        gains = {}
        score = 0
        visited_periods, missing_periods = _visit_status(tile, period_tiles)

        for index, (key, label, period, kind) in enumerate(PRIORITIES):
            baseline = baselines[period]
            hit = False

            if kind == "unvisited":
                hit = tile not in baseline["tiles"]
            else:
                gain = _measure_gain(tile, baseline, kind)
                gains[key] = gain
                hit = gain > 0

            if hit:
                priority = index + 1
                score += 2 ** (len(PRIORITIES) - index)
                reasons.append({
                    "key": key,
                    "label": label,
                    "period": period,
                    "priority": priority,
                    "gain": gains.get(key, 1),
                })

        if not reasons:
            continue

        first_reason = reasons[0]
        opportunities.append({
            "tile": tile,
            "score": score,
            "priority": first_reason["priority"],
            "top_reason": first_reason["label"],
            "visited_periods": visited_periods,
            "missing_periods": missing_periods,
            "reasons": reasons,
            "gains": gains,
        })

    opportunities.sort(key=lambda item: (-item["score"], item["priority"], item["tile"]))
    for rank, opportunity in enumerate(opportunities, start=1):
        opportunity["rank"] = rank

    return opportunities
