"""Planovani vypravy: [beh na zastavku] -> MHD -> beh -> MHD -> [beh domu].

Casovy rozpocet (budget_min) pokryva celou vypravu. Pesi presuny na zastavku a
zpet se planuji po stejnem pesim grafu jako behy a pocitaji se do kilometru
behu. Navrat muze vest z jine zastavky nez vystup (beh z bodu do bodu pres
cilovou oblast). Cekani na spoje vychazi z intervalu linek pro dany typ dne
(vsedni den / vikend). Cista varianta bez MHD se porovnava vzdy; MHD varianty
se nejdriv levne ohodnoti odhadem a exaktne se planuji jen nejlepsi.
"""
import math

from geo import haversine_m, tile_center
from routeplan import candidate_groups, plan_tile_loop, plan_walk
from scoring import evaluate_tile_set
from waygraph import covering_graph_path, load_walk_graph

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
# Kolik kandidatu se doopravdy naplanuje. Odhad sklizne (_harvest_estimate) je
# jen hrube razeni - mereno proti trem skutecnym trasam nadhodnocuje cile MHD
# nekolikanasobne, takze spolehat na prvni tri by dobre kandidaty ustrihlo.
# Kandidati s uz stazenym grafem jsou levni (bez cekani na Overpass) a po
# uvolneni predfiltru jich vetsinou par je, takze se jich vyplati projit vic.
MAX_EXACT_TRANSIT_PLANS = 5
MIN_LOOP_KM = 3.0
MIN_TARGET_BENEFIT = 20.0
MAX_TARGET_SPAN_TILES = 6       # vetsi skupiny nejsou cil jednoho behu - deli se
TARGET_CELL_TILES = 4           # mrizka deleni (~6 km)
# Jak daleko musi cil byt, aby melo smysl uvazovat MHD - podil cilove delky behu.
# Drive se vyrazovalo vsechno blizsi nez polovina dosahu behu s oduvodnenim
# "tam si doberhnes sam". To je ale spatna uvaha: dobehnout k oblasti 6 km daleko
# a zpatky spotrebuje 12 z 15 km rozpoctu a na sbirani v oblasti nezbyde nic.
# Mereno z Karlova nam.: Strasnicka (5,5 km), Skalka (6,3 km) i Depo Hostivar
# (7,4 km) lezely pod starym prahem 8,1 km, pritom metrem A jsou za 9-16 minut
# a cela vyprava vyjde na 105-112 minut ze 120. Ted se vyrazuje jen to, co je tak
# blizko, ze cesta na zastavku stoji vic, nez usetri.
MIN_TRANSIT_TARGET_SHARE = 0.2
# Odhad, kolik dlazdic trasa dane delky protne (mereno na referencnich okruzich).
TILES_PER_KM = 0.7
# Jednosmerna vyprava (MHD tam, beh domu) ma smysl, jen kdyz je domov v dosahu
# delkoveho okna. Podil, ne cela delka: trasa musi mit rezervu na zajizdky za
# dlazdicemi, jinak by byla jen prima spojnicí domu.
ONEWAY_HOME_SHARE = 0.75


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
    for component in candidate_groups(candidates):
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


def _opportunity_points(opportunities):
    """(lat, lon, skore, tile) pro kazdou prilezitost - pocita se jednou."""
    points = []
    for item in opportunities:
        tile = tuple(item["tile"])
        lat, lon = tile_center(tile)
        points.append((lat, lon, item["score"], tile))
    return points


def _harvest_estimate(lat, lon, loop_km, points, context):
    """Odhad prinosu, ktery beh dane delky z tohoto bodu posbira.

    Nahrazuje razeni podle prinosu CELE cilove oblasti. Ten byl zavadejici ve
    dvou smerech: velka oblast ma vysoky soucet, ale beh z ni stihne jen kousek
    (Zbuzany: odhad oblasti 1229, skutecna trasa nic), kdezto blizka oblast ma
    soucet maly, i kdyz by z ni beh nasbiral dost.

    Bere nejlepsi dlazdice v dosahu okruhu a ohodnoti je JAKO MNOZINU
    (evaluate_tile_set) - zisky square/cluster nejsou aditivni, takze soucet
    jednotlivych skore by poradi opet zkreslil.
    """
    reach_m = loop_km * 1000 / 2 * 0.9
    near = [
        (score, tile) for plat, plon, score, tile in points
        if haversine_m(lat, lon, plat, plon) <= reach_m
    ]
    if not near:
        return 0.0
    near.sort(reverse=True)
    take = max(1, int(loop_km * TILES_PER_KM))
    return evaluate_tile_set({tile for _score, tile in near[:take]}, context)["total"]


def _transit_candidates(start_lat, start_lon, target_km, tolerance_km, budget_min, pace,
                        network, targets, origin_ids, day, opportunities, context):
    # predfiltr dosazitelnosti: cile, ke kterym se pri nejkratsim pripustnem behu
    # vubec da dojet a vratit se v rozpoctu (hruby odhad rychlosti MHD)
    min_run_min = max(target_km - tolerance_km, MIN_LOOP_KM) * pace
    transit_budget_min = (budget_min - min_run_min) / 2
    if transit_budget_min <= 0:
        return []
    max_target_m = (transit_budget_min / 60 * TRANSIT_SPEED_KMH + (target_km + tolerance_km) / 2) * 1000

    direct_reach_m = target_km * 1000 * MIN_TRANSIT_TARGET_SHARE
    eligible = [
        target for target in targets
        if direct_reach_m < haversine_m(start_lat, start_lon, target["lat"], target["lon"]) <= max_target_m
    ]

    # Poradi rozhoduje, co se vejde do MAX_TARGET_CHECKS dotazu na spojeni -
    # radime proto podle odhadu SKLIZNE, ne podle prinosu cele oblasti.
    points = _opportunity_points(opportunities)
    for target in eligible:
        target["harvest"] = _harvest_estimate(
            target["lat"], target["lon"], target_km, points, context
        )
    eligible.sort(key=lambda target: -target["harvest"])

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
            # ted uz je zname misto vystupu i skutecna delka okruhu, takze se
            # sklizen prepocita presneji nez u stredu cilove oblasti
            "harvest": _harvest_estimate(alight[1], alight[2], window[0], points, context),
        })

    candidates.sort(key=lambda item: -item["harvest"])
    return candidates


def _plan_pure_loop(start_lat, start_lon, target_km, tolerance_km, budget_min, pace, opportunities,
                    context, quiet_weight=None):
    window = _loop_window(target_km, tolerance_km, 0.0, budget_min, 0.0, pace)
    if window is None:
        return None
    loop_target, loop_tolerance = window
    reach_km = (loop_target + loop_tolerance) / 2 + 0.5
    graph = load_walk_graph(start_lat, start_lon, reach_km)
    route = plan_tile_loop(graph, start_lat, start_lon, loop_target, loop_tolerance, opportunities,
                           context, quiet_weight=quiet_weight)
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


def _plan_oneway_expedition(candidate, start_lat, start_lon, pace, budget_min,
                            opportunities, context, quiet_weight=None):
    """MHD tam, beh domu - bez zpatecni jizdy.

    Vyhodnejsi tvar vypravy, kdykoli se domu doběhnout da: usetri celou jednu
    cestu MHD a ten cas se prelije do behu, takze se v cilove oblasti stihne
    vic. Mereno z Karlova nam. na Sidliste Zahradni Mesto: okruh se zpatecni
    jizdou dal 10,8 km behu a 16 minut cekani a jizdy navic, pritom beh koncil
    7 km od domova.

    Delkove okno se pocita jen s jednou jizdou a jednim dobehem na zastavku,
    takze vyjde vyrazne sirsi nez u okruhu.
    """
    board = candidate["board"]
    alight = candidate["alight"]
    conn_out = candidate["connection"]

    home_graph = load_walk_graph(start_lat, start_lon, HOME_WALK_REACH_KM)
    walk_out = plan_walk(home_graph, start_lat, start_lon, board["lat"], board["lon"])

    window = _loop_window(
        candidate["target_km"], candidate["tolerance_km"], walk_out["km"],
        budget_min, conn_out["minutes"], pace,
    )
    if window is None:
        raise RuntimeError("One-way expedition does not fit the time budget")
    run_target, run_tolerance = window

    # Domu se musi dat dobehnout v ramci delkoveho okna - jinak je jednosmerny
    # tvar nesmysl a musi se vracet MHD.
    home_km = haversine_m(alight["lat"], alight["lon"], start_lat, start_lon) / 1000
    if home_km > (run_target + run_tolerance) * ONEWAY_HOME_SHARE:
        raise RuntimeError("Too far to run home")

    reach_km = (run_target + run_tolerance) / 2 + home_km / 2 + 0.5
    graph = load_walk_graph(
        (alight["lat"] + start_lat) / 2, (alight["lon"] + start_lon) / 2, reach_km
    )
    route = plan_tile_loop(
        graph, alight["lat"], alight["lon"], run_target, run_tolerance,
        opportunities, context, end_lat=start_lat, end_lon=start_lon,
        quiet_weight=quiet_weight,
    )

    run_km = round(route["length_km"] + walk_out["km"], 2)
    total_min = round(run_km * pace + conn_out["minutes"], 1)

    return {
        "kind": "transit",
        "benefit": route["benefit"],
        "run_km": run_km,
        "total_min": total_min,
        "within_budget": bool(total_min <= budget_min),
        "board": board,
        "alight": alight,
        "return_stop": None,  # domu se bezi, zpatecni jizda neni
        "segments": [
            _walk_segment(walk_out, pace, f"Behem na zastavku {board['name']}"),
            _transit_segment(conn_out, f"{board['name']} -> {alight['name']}"),
            _run_segment(route, pace),
        ],
        "route": route,
    }


def _plan_transit_expedition(candidate, start_lat, start_lon, pace, budget_min,
                             opportunities, context, network, origin_ids, day,
                             quiet_weight=None):
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
        quiet_weight=quiet_weight,
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
                    opportunities, context, network, targets, day="weekday",
                    quiet_weight=None):
    home_stops = network.stops_near(start_lat, start_lon, HOME_STOP_RADIUS_M)[:HOME_STOP_LIMIT]
    origin_ids = [stop_id for _, stop_id in home_stops]

    candidates = []
    if origin_ids:
        candidates = _transit_candidates(
            start_lat, start_lon, target_km, tolerance_km, budget_min, pace,
            network, targets, origin_ids, day, opportunities, context,
        )

    # exaktne planuj: kandidaty s uz stazenym grafem maji prednost (bez cekani)
    def has_cached_graph(candidate):
        reach = (candidate["loop_target"] + candidate["loop_tolerance"]) / 2 + 0.5
        return covering_graph_path(candidate["alight"]["lat"], candidate["alight"]["lon"], reach) is not None

    ordered = sorted(candidates, key=lambda c: (not has_cached_graph(c), -c["harvest"]))

    plans = []
    pure = _plan_pure_loop(
        start_lat, start_lon, target_km, tolerance_km, budget_min, pace, opportunities, context,
        quiet_weight=quiet_weight,
    )
    if pure:
        plans.append(pure)

    # Nejdriv jednosmerny tvar (MHD tam, beh domu) - kdykoli se domu dobehnout
    # da, je vyhodnejsi: usetri celou jednu jizdu a ten cas se prelije do behu.
    # Okruh se zpatecni jizdou se pocita az jako nahrada pro cile, ze kterych se
    # domu dobehnout neda. Planovat oba tvary pro kazdeho kandidata by dobu
    # planovani zdvojnasobilo.
    for candidate in ordered[:MAX_EXACT_TRANSIT_PLANS]:
        try:
            plans.append(_plan_oneway_expedition(
                candidate, start_lat, start_lon, pace, budget_min,
                opportunities, context, quiet_weight=quiet_weight,
            ))
            continue
        except RuntimeError:
            pass
        try:
            plans.append(_plan_transit_expedition(
                candidate, start_lat, start_lon, pace, budget_min,
                opportunities, context, network, origin_ids, day,
                quiet_weight=quiet_weight,
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
            "benefit_estimate": round(candidate["harvest"], 1),
            "lines": [leg["line"] for leg in candidate["connection"]["legs"]],
        }
        for candidate in candidates[:6]
    ]
    best["budget_min"] = budget_min
    best["pace_min_per_km"] = pace
    best["day"] = day
    return best
