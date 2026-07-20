from datetime import date

from cluster import find_largest_cluster
from geojson import tile_xy
from square import find_largest_square


STALENESS_CAP_DAYS = 3 * 365

# Vahy: pomer 4:2:1 uvnitr obdobi (square : cluster : nenavstiveny) a 16x mezi
# obdobimi - vyrazny odstup celkove > letosni > 3mesicni dle preference
# uzivatele (2026-07-19), aby napr. rust rocniho clusteru prevazil 3mesicni
# square. Vahy se nasobi velikosti zisku (square plochou side^2 - baseline^2).
# Minimalni vaha 2 drzi staleness bonus (<= 1) pod rozlisovaci schopnosti priorit.
PRIORITIES = [
    ("all_square", "Zvetsi celkovy max square", "all", "square", 2048),
    ("all_cluster", "Zvetsi celkovy max cluster", "all", "cluster", 1024),
    ("all_unvisited", "Celkove nenavstiveny tile", "all", "unvisited", 512),
    ("year_square", "Zvetsi letosni max square", "year", "square", 128),
    ("year_cluster", "Zvetsi letosni max cluster", "year", "cluster", 64),
    ("year_unvisited", "Letos nenavstiveny tile", "year", "unvisited", 32),
    ("recent_square", "Zvetsi 3mesicni max square", "recent", "square", 8),
    ("recent_cluster", "Zvetsi 3mesicni max cluster", "recent", "cluster", 4),
    ("recent_unvisited", "Za posledni 3 mesice nenavstiveny tile", "recent", "unvisited", 2),
]

PRIORITY_WEIGHTS = {key: weight for key, _label, _period, _kind, weight in PRIORITIES}


def _tile_set(tile_db):
    return {tile_xy(tile) for tile in tile_db}


def _staleness_bonus(days_since_visit):
    """Bonus 0..1 za stari posledni navstevy — cisty prinos tile, zadne naklady
    na cestu. Drzi se pod 2, coz je minimalni rozestup mezi ruznymi kombinacemi
    priorit, takze meni poradi jen mezi tiles se shodnymi prioritami."""
    if days_since_visit is None:
        return 1.0
    return min(max(days_since_visit, 0) / STALENESS_CAP_DAYS, 1.0)


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


def build_route_context(period_tile_dbs, today=None):
    """Predpocitane podklady pro vyhodnocovani prinosu mnoziny tiles."""
    period_tiles = {
        period: _tile_set(tile_db)
        for period, tile_db in period_tile_dbs.items()
    }
    return {
        "period_tiles": period_tiles,
        "baselines": {
            period: _period_baseline(tiles)
            for period, tiles in period_tiles.items()
        },
        "last_visits": {
            tile_xy(tile): rec["last_visit"]
            for tile, rec in period_tile_dbs["all"].items()
        },
        "today": today or date.today(),
    }


def evaluate_tile_set(tiles, context):
    """Spolecny prinos navstevy cele mnoziny tiles v jednom behu.

    Zisky square/cluster se pocitaji s celou mnozinou najednou - nejsou aditivni
    pres jednotlive tiles (skupina sousedu muze zvetsit square, i kdyz zadny z
    nich samostatne ne). Vaha priority x velikost zisku + staleness bonusy.
    Square se vazi PLOCHOU (side^2 - baseline^2): rust strany je vzacny a bez
    toho by ho snadny rust clusteru o par tiles vzdy prebil, coz obraci poradi
    priorit. V gains zustava rozdil strany (pro zobrazeni).
    """
    tiles = {tile_xy(tile) for tile in tiles}
    total = 0.0
    gains = {}

    for key, _label, period, kind, weight in PRIORITIES:
        existing = context["period_tiles"][period]
        baseline = context["baselines"][period]

        if kind == "unvisited":
            gain = sum(1 for tile in tiles if tile not in existing)
            contribution = gain
        else:
            added = tiles - existing
            gain = 0
            contribution = 0
            if added:
                expanded = existing | added
                if kind == "cluster":
                    gain = find_largest_cluster(expanded)["size"] - baseline["cluster_size"]
                    contribution = gain
                else:
                    new_side = find_largest_square(expanded)["size"]
                    gain = new_side - baseline["square_size"]
                    contribution = new_side ** 2 - baseline["square_size"] ** 2

        gains[key] = gain
        total += weight * contribution

    staleness = 0.0
    for tile in tiles:
        last_visit = context["last_visits"].get(tile)
        days = (context["today"] - last_visit.date()).days if last_visit else None
        staleness += _staleness_bonus(days)

    return {
        "total": round(total + staleness, 3),
        "gains": gains,
        "staleness": round(staleness, 3),
    }


def find_tile_opportunities(period_tile_dbs, today=None):
    today = today or date.today()
    last_visits = {
        tile_xy(tile): rec["last_visit"]
        for tile, rec in period_tile_dbs["all"].items()
    }
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

        for index, (key, label, period, kind, weight) in enumerate(PRIORITIES):
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
                score += weight
                reasons.append({
                    "key": key,
                    "label": label,
                    "period": period,
                    "priority": priority,
                    "gain": gains.get(key, 1),
                })

        if not reasons:
            continue

        last_visit = last_visits.get(tile)
        days_since_visit = (today - last_visit.date()).days if last_visit else None

        first_reason = reasons[0]
        opportunities.append({
            "tile": tile,
            "score": round(score + _staleness_bonus(days_since_visit), 3),
            "priority": first_reason["priority"],
            "top_reason": first_reason["label"],
            "last_visit": last_visit.date().isoformat() if last_visit else None,
            "days_since_visit": days_since_visit,
            "visited_periods": visited_periods,
            "missing_periods": missing_periods,
            "reasons": reasons,
            "gains": gains,
        })

    opportunities.sort(key=lambda item: (-item["score"], item["priority"], item["tile"]))
    for rank, opportunity in enumerate(opportunities, start=1):
        opportunity["rank"] = rank

    return opportunities
