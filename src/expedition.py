"""Planovani vypravy: [beh na zastavku] -> MHD -> beh (okruh) -> MHD -> [beh domu].

Casovy rozpocet (budget_min) pokryva celou vypravu. Pesi presuny na zastavku a
zpet se pocitaji do kilometru behu. Cista varianta bez MHD (okruh primo ze
startu) se porovnava vzdy; MHD varianty se nejdriv levne ohodnoti odhadem
(spolecny prinos cilove oblasti + cas spojeni) a exaktne se planuji jen nejlepsi.
Zpatecni spojeni se v teto verzi uvazuje symetricke (stejna zastavka a cas).
"""
import math

from routing import (
    _candidate_groups,
    _covering_graph_path,
    haversine_m,
    load_walk_graph,
    plan_tile_loop,
    tile_center,
)
from scoring import evaluate_tile_set

WALK_DETOUR = 1.3
HOME_STOP_RADIUS_M = 1500
HOME_STOP_LIMIT = 12
TARGET_STOP_LIMIT = 15
MAX_TARGETS = 20
TRANSIT_SPEED_KMH = 40  # hruby horni odhad rychlosti MHD vc. cekani (pro predfiltr)
MAX_EXACT_TRANSIT_PLANS = 2
MIN_LOOP_KM = 3.0
MIN_TARGET_BENEFIT = 20.0
MAX_TARGET_SPAN_TILES = 6   # vetsi skupiny nejsou cil jednoho behu - deli se
TARGET_CELL_TILES = 4       # mrizka deleni (~6 km)


def _split_group(group):
    """Skupiny vetsi nez dosah jednoho behu rozdel na lokalni kusy po mrizce.
    (Souvisla fronta kolem celeho uzemi jinak tvori jedinou obri 'skupinu'
    s nesmyslnym centroidem i prinosem.)"""
    xs = [member["tile"][0] for member in group]
    ys = [member["tile"][1] for member in group]
    if max(xs) - min(xs) <= MAX_TARGET_SPAN_TILES and max(ys) - min(ys) <= MAX_TARGET_SPAN_TILES:
        return [group]

    buckets = {}
    for member in group:
        x, y = member["tile"]
        buckets.setdefault((x // TARGET_CELL_TILES, y // TARGET_CELL_TILES), []).append(member)
    return list(buckets.values())


def _square_window_targets(context, max_missing=4, per_period=8):
    """Okna (side+1)^2 pro zvetseni max square jako samostatne cilove oblasti.

    Chybejici tiles okna byvaji od sebe daleko (ruzne bunky mrizkoveho deleni
    skupin) a jednotlive maji nulovy square prinos, takze by je skupinove cile
    nikdy nezachytily - viz pripad Cernosice/Solopisky (2 tiles pro 16x16).
    """
    import numpy as np

    all_tiles = context["period_tiles"]["all"]
    if not all_tiles:
        return []
    xs = [x for x, _ in all_tiles]
    ys = [y for _, y in all_tiles]
    min_x, min_y = min(xs), min(ys)
    width = max(xs) - min_x + 1
    height = max(ys) - min_y + 1

    targets = []
    for period in ("all", "year", "recent"):
        tiles = context["period_tiles"][period]
        side = context["baselines"][period]["square_size"] + 1
        if not tiles or side < 2 or width < side or height < side:
            continue

        occupancy = np.zeros((width + 1, height + 1), dtype=np.int32)
        for x, y in tiles:
            occupancy[x - min_x + 1, y - min_y + 1] = 1
        integral = occupancy.cumsum(axis=0).cumsum(axis=1)
        covered = (integral[side:, side:] - integral[:-side, side:]
                   - integral[side:, :-side] + integral[:-side, :-side])
        missing_counts = side * side - covered
        hits = np.argwhere((missing_counts <= min(max_missing, side - 1)) & (covered > 0))
        if not len(hits):
            continue

        order = np.argsort(missing_counts[tuple(hits.T)])
        seen = set()
        for index in order:
            ax, ay = (int(v) for v in hits[index])
            ax, ay = ax + min_x, ay + min_y
            window = [(ax + dx, ay + dy) for dx in range(side) for dy in range(side)]
            missing = tuple(sorted(tile for tile in window if tile not in tiles))
            if not missing or missing in seen:
                continue
            seen.add(missing)
            if len(seen) > per_period:
                break

            benefit = evaluate_tile_set(set(missing), context)
            centers = [tile_center(tile) for tile in missing]
            targets.append({
                "tiles": list(missing),
                "size": len(missing),
                "lat": round(sum(c[0] for c in centers) / len(centers), 5),
                "lon": round(sum(c[1] for c in centers) / len(centers), 5),
                "benefit": benefit["total"],
                "gains": {k: v for k, v in benefit["gains"].items() if v},
            })
    return targets


def build_targets(opportunities, context):
    """Cilove oblasti = lokalni skupiny sousednich kandidatu se spolecnym prinosem."""
    candidates = [
        {"tile": tuple(item["tile"]), "score": item["score"]}
        for item in opportunities
    ]
    for cand in candidates:
        lat, lon = tile_center(cand["tile"])
        cand["lat"], cand["lon"] = lat, lon

    targets = []
    for component in _candidate_groups(candidates):
        for group in _split_group(component):
            tiles = {member["tile"] for member in group}
            benefit = evaluate_tile_set(tiles, context)
            if benefit["total"] < MIN_TARGET_BENEFIT:
                continue
            lat = sum(member["lat"] for member in group) / len(group)
            lon = sum(member["lon"] for member in group) / len(group)
            targets.append({
                "tiles": sorted(tiles),
                "size": len(group),
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "benefit": benefit["total"],
                "gains": {k: v for k, v in benefit["gains"].items() if v},
            })

    targets.extend(_square_window_targets(context))
    targets.sort(key=lambda item: -item["benefit"])
    return targets


def _run_segment(route, pace_min_per_km):
    return {
        "type": "run",
        "km": route["length_km"],
        "min": round(route["length_km"] * pace_min_per_km, 1),
    }


def _loop_window(target_km, tolerance_km, walks_km, budget_min, transit_min, pace):
    """Okno delky okruhu tak, aby beh (vcetne pesich presunu) splnil delkovou
    toleranci i casovy rozpocet."""
    run_minutes = budget_min - 2 * transit_min
    max_by_time = run_minutes / pace - walks_km
    loop_max = min(target_km + tolerance_km - walks_km, max_by_time)
    loop_min = target_km - tolerance_km - walks_km
    if loop_max < MIN_LOOP_KM or loop_max < loop_min:
        return None
    return (max(loop_min, MIN_LOOP_KM) + loop_max) / 2, (loop_max - max(loop_min, MIN_LOOP_KM)) / 2


def _transit_candidates(start_lat, start_lon, target_km, tolerance_km, budget_min, pace, network, targets):
    home_stops = network.stops_near(start_lat, start_lon, HOME_STOP_RADIUS_M)[:HOME_STOP_LIMIT]
    if not home_stops:
        return []
    origin_ids = [stop_id for _, stop_id in home_stops]

    # predfiltr dosazitelnosti: cile, ke kterym se pri nejkratsim pripustnem behu
    # vubec da dojet a vratit se v rozpoctu (hruby odhad rychlosti MHD)
    min_run_min = max(target_km - tolerance_km, MIN_LOOP_KM) * pace
    transit_budget_min = (budget_min - min_run_min) / 2
    if transit_budget_min <= 0:
        return []
    max_target_m = (transit_budget_min / 60 * TRANSIT_SPEED_KMH + (target_km + tolerance_km) / 2) * 1000

    direct_reach_m = (target_km + tolerance_km) * 1000 / 2 * 0.9
    eligible = [
        target for target in targets
        if direct_reach_m < haversine_m(start_lat, start_lon, target["lat"], target["lon"]) <= max_target_m
    ]

    candidates = []
    seen_stops = set()
    for target in eligible[:MAX_TARGETS]:

        # zastavka musi byt tak blizko cile, aby ho okruh obsahl
        stop_radius = (target_km + tolerance_km) * 1000 / 2 * 0.8
        stops = network.stops_near(target["lat"], target["lon"], stop_radius)[:TARGET_STOP_LIMIT]
        if not stops:
            continue

        connection = network.route(origin_ids, [stop_id for _, stop_id in stops])
        if not connection or not connection["legs"]:
            continue
        if connection["stop_id"] in seen_stops:
            continue
        seen_stops.add(connection["stop_id"])

        board_id = connection["legs"][0]["from_id"]
        board = network.stops[board_id]
        alight = network.stops[connection["stop_id"]]
        walk_km = haversine_m(start_lat, start_lon, board[1], board[2]) * WALK_DETOUR / 1000

        window = _loop_window(
            target_km, tolerance_km, 2 * walk_km, budget_min, connection["minutes"], pace
        )
        if window is None:
            continue

        candidates.append({
            "target": target,
            "connection": connection,
            "board": {"id": board_id, "name": board[0], "lat": board[1], "lon": board[2]},
            "alight": {"id": connection["stop_id"], "name": alight[0], "lat": alight[1], "lon": alight[2]},
            "walk_km": round(walk_km, 2),
            "loop_target": window[0],
            "loop_tolerance": window[1],
        })

    candidates.sort(key=lambda item: -item["target"]["benefit"])
    return candidates


def _plan_pure_loop(start_lat, start_lon, target_km, tolerance_km, budget_min, pace, opportunities, context):
    window = _loop_window(target_km, tolerance_km, 0.0, budget_min, 0.0, pace)
    if window is None:
        return None
    loop_target, loop_tolerance = window
    reach_km = (loop_target + loop_tolerance) / 2 + 0.5
    graph = load_walk_graph(start_lat, start_lon, reach_km)
    route = plan_tile_loop(graph, start_lat, start_lon, loop_target, loop_tolerance, opportunities, context)
    total_min = route["length_km"] * pace
    return {
        "kind": "loop",
        "benefit": route["benefit"],
        "run_km": route["length_km"],
        "total_min": round(total_min, 1),
        "within_budget": total_min <= budget_min,
        "segments": [_run_segment(route, pace)],
        "route": route,
    }


def _plan_transit_expedition(candidate, start_lat, start_lon, pace, budget_min, opportunities, context):
    alight = candidate["alight"]
    reach_km = (candidate["loop_target"] + candidate["loop_tolerance"]) / 2 + 0.5
    graph = load_walk_graph(alight["lat"], alight["lon"], reach_km)
    route = plan_tile_loop(
        graph, alight["lat"], alight["lon"],
        candidate["loop_target"], candidate["loop_tolerance"],
        opportunities, context,
    )

    connection = candidate["connection"]
    walk_km = candidate["walk_km"]
    walk_min = round(walk_km * pace, 1)
    run_km = round(route["length_km"] + 2 * walk_km, 2)
    total_min = round(2 * walk_min + 2 * connection["minutes"] + route["length_km"] * pace, 1)

    walk_segment = {
        "type": "walk",
        "km": walk_km,
        "min": walk_min,
        "from": {"lat": start_lat, "lon": start_lon},
        "to": {"lat": candidate["board"]["lat"], "lon": candidate["board"]["lon"]},
        "desc": f"Behem na zastavku {candidate['board']['name']}",
    }
    transit_segment = {
        "type": "transit",
        "min": connection["minutes"],
        "transfers": connection["transfers"],
        "legs": connection["legs"],
        "desc": f"{candidate['board']['name']} -> {alight['name']}",
    }
    return {
        "kind": "transit",
        "benefit": route["benefit"],
        "run_km": run_km,
        "total_min": total_min,
        "within_budget": total_min <= budget_min,
        "board": candidate["board"],
        "alight": alight,
        "segments": [
            walk_segment,
            transit_segment,
            _run_segment(route, pace),
            {**transit_segment, "desc": f"{alight['name']} -> {candidate['board']['name']} (navrat)"},
            {**walk_segment, "desc": "Behem zpet na start",
             "from": walk_segment["to"], "to": walk_segment["from"]},
        ],
        "route": route,
    }


def plan_expedition(start_lat, start_lon, target_km, tolerance_km, budget_min, pace,
                    opportunities, context, network, targets):
    candidates = _transit_candidates(
        start_lat, start_lon, target_km, tolerance_km, budget_min, pace, network, targets
    )

    # exaktne planuj: kandidaty s uz stazenym grafem maji prednost (bez cekani)
    def has_cached_graph(candidate):
        reach = (candidate["loop_target"] + candidate["loop_tolerance"]) / 2 + 0.5
        return _covering_graph_path(candidate["alight"]["lat"], candidate["alight"]["lon"], reach) is not None

    ordered = sorted(candidates, key=lambda c: (not has_cached_graph(c), -c["target"]["benefit"]))

    plans = []
    pure = _plan_pure_loop(
        start_lat, start_lon, target_km, tolerance_km, budget_min, pace, opportunities, context
    )
    if pure:
        plans.append(pure)

    for candidate in ordered[:MAX_EXACT_TRANSIT_PLANS]:
        try:
            plans.append(_plan_transit_expedition(
                candidate, start_lat, start_lon, pace, budget_min, opportunities, context
            ))
        except RuntimeError:
            continue

    if not plans:
        raise RuntimeError("Zadna vyprava se do rozpoctu nevejde")

    plans.sort(key=lambda plan: (not plan["within_budget"], -plan["benefit"]["total"], plan["total_min"]))
    best = plans[0]
    best["alternatives"] = [
        {
            "alight": candidate["alight"]["name"],
            "transit_min": candidate["connection"]["minutes"],
            "transfers": candidate["connection"]["transfers"],
            "benefit_estimate": candidate["target"]["benefit"],
            "lines": [leg["line"] for leg in candidate["connection"]["legs"]],
        }
        for candidate in candidates[:6]
    ]
    best["budget_min"] = budget_min
    best["pace_min_per_km"] = pace
    return best
