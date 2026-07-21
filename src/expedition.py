"""Planovani vypravy: [beh na zastavku] -> MHD -> beh -> MHD -> [beh domu].

Casovy rozpocet (budget_min) pokryva celou vypravu. Pesi presuny na zastavku a
zpet se planuji po stejnem pesim grafu jako behy a pocitaji se do kilometru
behu. Navrat muze vest z jine zastavky nez vystup (beh z bodu do bodu pres
cilovou oblast). Cekani na spoje vychazi z intervalu linek pro dany typ dne
(vsedni den / vikend). Cista varianta bez MHD se porovnava vzdy; MHD varianty
se nejdriv levne ohodnoti odhadem a exaktne se planuji jen nejlepsi.
"""
import math

from routing import (
    _candidate_groups,
    _covering_graph_path,
    haversine_m,
    load_walk_graph,
    plan_tile_loop,
    plan_walk,
    tile_center,
)
from scoring import evaluate_tile_set

WALK_DETOUR = 1.3               # jen pro levne odhady pri screeningu
HOME_WALK_REACH_KM = 3.0
HOME_STOP_RADIUS_M = 2000  # dobeh na nadrazi je soucasti behu, muze byt stedry
HOME_STOP_LIMIT = 12
TARGET_STOP_LIMIT = 15
RETURN_STOP_LIMIT = 8
RETURN_COST_SLACK = 15.0        # navrat smi byt o tolik "drazsi", kdyz umozni prechod oblasti
MAX_TARGETS = 20                # strop CASOVE SPLNITELNYCH kandidatu
MAX_TARGET_CHECKS = 40          # strop dotazu na spojeni (nesplnitelne cile sloty nespotrebuji)
TRANSIT_SPEED_KMH = 40          # hruby horni odhad rychlosti MHD vc. cekani (pro predfiltr)
MAX_EXACT_TRANSIT_PLANS = 3
MIN_LOOP_KM = 3.0
MIN_TARGET_BENEFIT = 20.0
MAX_TARGET_SPAN_TILES = 6       # vetsi skupiny nejsou cil jednoho behu - deli se
TARGET_CELL_TILES = 4           # mrizka deleni (~6 km)


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
    """Cilove oblasti = lokalni skupiny sousednich kandidatu se spolecnym
    prinosem + okna na dokompletovani max square."""
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


def _walk_segment(walk, pace, desc):
    return {
        "type": "walk",
        "km": walk["km"],
        "min": round(walk["km"] * pace, 1),
        "coordinates": walk["coordinates"],
        "desc": desc,
    }


def _transit_segment(connection, desc):
    return {
        "type": "transit",
        "min": connection["minutes"],
        "transfers": connection["transfers"],
        "legs": connection["legs"],
        "desc": desc,
    }


def _loop_window(target_km, tolerance_km, walks_km, budget_min, transit_total_min, pace):
    """Okno delky behu tak, aby beh (vcetne pesich presunu) splnil delkovou
    toleranci i casovy rozpocet."""
    run_minutes = budget_min - transit_total_min
    max_by_time = run_minutes / pace - walks_km
    loop_max = min(target_km + tolerance_km - walks_km, max_by_time)
    loop_min = target_km - tolerance_km - walks_km
    if loop_max < MIN_LOOP_KM or loop_max < loop_min:
        return None
    return (max(loop_min, MIN_LOOP_KM) + loop_max) / 2, (loop_max - max(loop_min, MIN_LOOP_KM)) / 2


def _home_walk_costs(network, start_lat, start_lon, stop_ids, pace):
    """Cena dobehu mezi domovem a zastavkou v minutach behu - router pak vybira
    zastavku vyhodnou pro celou vypravu (blizsi zastavka porazi vzdalenejsi se
    stejne rychlym spojenim)."""
    costs = {}
    for stop_id in stop_ids:
        _name, lat, lon = network.stops[stop_id]
        costs[stop_id] = haversine_m(start_lat, start_lon, lat, lon) * WALK_DETOUR / 1000 * pace
    return costs


def _transit_candidates(start_lat, start_lon, target_km, tolerance_km, budget_min, pace,
                        network, targets, origin_ids, day):
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
    for target in eligible[:MAX_TARGET_CHECKS]:
        if len(candidates) >= MAX_TARGETS:
            break
        # zastavka musi byt tak blizko cile, aby ho beh obsahl
        stop_radius = (target_km + tolerance_km) * 1000 / 2 * 0.8
        stops = network.stops_near(target["lat"], target["lon"], stop_radius)[:TARGET_STOP_LIMIT]
        if not stops:
            continue

        connection = network.route(
            origin_ids, [stop_id for _, stop_id in stops], day=day,
            origin_costs=_home_walk_costs(network, start_lat, start_lon, origin_ids, pace),
        )
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
            target_km, tolerance_km, 2 * walk_km, budget_min, 2 * connection["minutes"], pace
        )
        if window is None:
            continue

        candidates.append({
            "target": target,
            "target_km": target_km,
            "tolerance_km": tolerance_km,
            "stop_radius": stop_radius,
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
    total_min = round(route["length_km"] * pace, 1)
    return {
        "kind": "loop",
        "benefit": route["benefit"],
        "run_km": route["length_km"],
        "total_min": total_min,
        "within_budget": bool(total_min <= budget_min),
        "segments": [_run_segment(route, pace)],
        "route": route,
    }


def _return_options(network, candidate, origin_ids, day, home_costs):
    """Zpatecni spojeni: prednostne zastavka s dobrym spojenim domu co nejdal
    od vystupu (beh cilovou oblast prejde), zaloznì nejlevnejsi navrat - pri
    tesnem rozpoctu se vzdalenejsi navrat nemusi vejit do casoveho okna."""
    target = candidate["target"]
    stops = network.stops_near(target["lat"], target["lon"], candidate["stop_radius"])[:RETURN_STOP_LIMIT]

    options = []
    for _, stop_id in stops:
        connection = network.route([stop_id], origin_ids, day=day, target_costs=home_costs)
        if connection:
            options.append((stop_id, connection))
    if not options:
        return []

    best_cost = min(connection["cost"] for _, connection in options)
    viable = [
        (stop_id, connection) for stop_id, connection in options
        if connection["cost"] <= best_cost + RETURN_COST_SLACK
    ]
    alight = candidate["alight"]
    farthest = max(
        viable,
        key=lambda item: haversine_m(
            alight["lat"], alight["lon"], network.stops[item[0]][1], network.stops[item[0]][2]
        ),
    )
    cheapest = min(options, key=lambda item: item[1]["cost"])
    ordered = [farthest] + ([cheapest] if cheapest[0] != farthest[0] else [])
    return [
        {
            "stop": {
                "id": stop_id,
                "name": network.stops[stop_id][0],
                "lat": network.stops[stop_id][1],
                "lon": network.stops[stop_id][2],
            },
            "connection": connection,
        }
        for stop_id, connection in ordered
    ]


def _plan_transit_expedition(candidate, start_lat, start_lon, pace, budget_min,
                             opportunities, context, network, origin_ids, day):
    board = candidate["board"]
    alight = candidate["alight"]
    conn_out = candidate["connection"]

    home_graph = load_walk_graph(start_lat, start_lon, HOME_WALK_REACH_KM)
    walk_out = plan_walk(home_graph, start_lat, start_lon, board["lat"], board["lon"])

    options = _return_options(
        network, candidate, origin_ids, day,
        _home_walk_costs(network, start_lat, start_lon, origin_ids, pace),
    )
    if not options:
        raise RuntimeError("No return connection from the target area")

    window = None
    for returning in options:
        return_stop = returning["stop"]
        conn_back = returning["connection"]
        home_exit = network.stops[conn_back["stop_id"]]
        walk_back = plan_walk(home_graph, home_exit[1], home_exit[2], start_lat, start_lon)
        walks_km = walk_out["km"] + walk_back["km"]
        transit_total = conn_out["minutes"] + conn_back["minutes"]
        window = _loop_window(
            candidate["target_km"], candidate["tolerance_km"], walks_km, budget_min, transit_total, pace
        )
        if window is not None:
            break
    if window is None:
        raise RuntimeError("Expedition does not fit the time budget")
    run_target, run_tolerance = window

    span_km = haversine_m(alight["lat"], alight["lon"], return_stop["lat"], return_stop["lon"]) / 1000
    reach_km = (run_target + run_tolerance) / 2 + span_km / 2 + 0.5
    graph = load_walk_graph(
        (alight["lat"] + return_stop["lat"]) / 2, (alight["lon"] + return_stop["lon"]) / 2, reach_km
    )
    route = plan_tile_loop(
        graph, alight["lat"], alight["lon"], run_target, run_tolerance,
        opportunities, context, end_lat=return_stop["lat"], end_lon=return_stop["lon"],
    )

    run_km = round(route["length_km"] + walks_km, 2)
    total_min = round((route["length_km"] + walks_km) * pace + transit_total, 1)

    return {
        "kind": "transit",
        "benefit": route["benefit"],
        "run_km": run_km,
        "total_min": total_min,
        "within_budget": bool(total_min <= budget_min),
        "board": board,
        "alight": alight,
        "return_stop": return_stop,
        "segments": [
            _walk_segment(walk_out, pace, f"Behem na zastavku {board['name']}"),
            _transit_segment(conn_out, f"{board['name']} -> {alight['name']}"),
            _run_segment(route, pace),
            _transit_segment(conn_back, f"{return_stop['name']} -> {home_exit[0]} (navrat)"),
            _walk_segment(walk_back, pace, f"Behem domu ze zastavky {home_exit[0]}"),
        ],
        "route": route,
    }


def plan_expedition(start_lat, start_lon, target_km, tolerance_km, budget_min, pace,
                    opportunities, context, network, targets, day="weekday"):
    home_stops = network.stops_near(start_lat, start_lon, HOME_STOP_RADIUS_M)[:HOME_STOP_LIMIT]
    origin_ids = [stop_id for _, stop_id in home_stops]

    candidates = []
    if origin_ids:
        candidates = _transit_candidates(
            start_lat, start_lon, target_km, tolerance_km, budget_min, pace,
            network, targets, origin_ids, day,
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
                candidate, start_lat, start_lon, pace, budget_min,
                opportunities, context, network, origin_ids, day,
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
    best["day"] = day
    return best
