"""MHD sit z PID GTFS: kompaktni graf zastavek + router.

Router minimalizuje primarne pocet prestupu, sekundarne cas vazeny prioritou
dopravnich prostredku (metro > tramvaj > vlak > ostatni). Casy jizdy pochazeji
z jizdnich radu (reprezentativni spoj kazde linky a smeru), cekani na spoj je
polovina intervalu linky pro dany typ dne (vsedni den / vikend, z GTFS
calendar); kde interval neni znamy, pouzije se pausal podle druhu dopravy.
"""
import csv
import heapq
import io
import itertools
import json
import math
import statistics
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

_GOAL = object()  # virtualni cilovy uzel (viz TransitNetwork.route)

ROOT = Path(__file__).resolve().parents[1]
GTFS_ZIP = ROOT / "data" / "pid_gtfs.zip"
GRAPH_CACHE = ROOT / "data" / "transit_graph.json"
GTFS_URL = "https://data.pid.cz/PID_GTFS.zip"
GRAPH_VERSION = 4

# GTFS route_type -> nas druh dopravy
MODES = {0: "tram", 1: "metro", 2: "train", 3: "bus"}

# priorita druhu: nasobic casu jizdy (mensi = preferovany)
MODE_MULTIPLIER = {"metro": 1.0, "tram": 1.15, "train": 1.25, "bus": 1.5, "other": 1.6}
# pausalni cekani pri nastupu/prestupu (minuty) - fallback bez znameho intervalu
MODE_WAIT_MIN = {"metro": 2.0, "tram": 4.0, "train": 8.0, "bus": 6.0, "other": 6.0}
WAIT_MIN_CLAMP = (1.0, 20.0)  # cekani = interval/2, orezane do tohoto rozsahu
# Linka s jedinym spojem za den zadny interval nema - cekani na ni je fakticky
# neomezene, dostava proto strop (bez toho spadla na pausal podle druhu dopravy
# a router ji nabizel, jako by jezdila kazdych 12 minut).
SPARSE_LINE_WAIT_MIN = WAIT_MIN_CLAMP[1]
TRANSFER_PENALTY_MIN = 30.0  # velka vaha = nejdriv minimalizuj prestupy
TRANSFER_WALK_MAX_M = 250.0
# stejnojmenne zastavky (nastupiste, nadrazi) na sebe prestupuji do teto
# vzdalenosti - PID ma stejna jmena obci/zastavek i desitky km od sebe!
NAME_TRANSFER_MAX_M = 600.0

EARTH_RADIUS_M = 6371000.0


def _haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _parse_gtfs_time(value):
    hours, minutes, seconds = value.split(":")
    return int(hours) * 60 + int(minutes) + int(seconds) / 60


def download_gtfs(url=GTFS_URL):
    request = urllib.request.Request(url, headers={"User-Agent": "statshunters-route-planner"})
    GTFS_ZIP.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=120) as response:
        GTFS_ZIP.write_bytes(response.read())
    return GTFS_ZIP


def _read_csv(zip_file, name):
    with zip_file.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))


def _is_technical_stop(stop_id):
    """PID znaci technicke kolejove body (kilometrovniky, hranice kraju,
    'Pha hl.n. Lc...') prefixem T + cislo; bezne zastavky maji prefix U.
    Technicke body maji souradnice na trati misto nastupist - vlaky se pres ne
    kontrahuji, aby nevznikaly falesne zastavky."""
    return len(stop_id) > 1 and stop_id[0] == "T" and stop_id[1].isdigit()


def _median_headway(departures):
    """Median rozestupu po sobe jdoucich odjezdu (minuty), orezany na 60."""
    if len(departures) < 2:
        return None
    departures = sorted(departures)
    gaps = [b - a for a, b in zip(departures, departures[1:]) if 0 < b - a <= 120]
    if not gaps:
        return None
    return min(round(statistics.median(gaps), 1), 60.0)


def build_transit_graph(gtfs_zip=GTFS_ZIP):
    """Z GTFS postavi kompaktni graf: zastavky + hrany po sobe jdoucich zastavek
    reprezentativniho spoje kazde (linka, smer) + intervaly linek pro vsedni den
    a vikend (median rozestupu odjezdu z vychozi zastavky). Cachuje do JSON."""
    zip_file = zipfile.ZipFile(gtfs_zip)

    routes = {}
    for row in _read_csv(zip_file, "routes.txt"):
        # Nocni linky (is_night) se pro planovani behu nehodi - jezdi v noci a
        # casto jen par spoju za noc.
        if row.get("is_night") == "1":
            continue
        route_type = int(row.get("route_type") or 3)
        routes[row["route_id"]] = {
            "name": row.get("route_short_name") or row["route_id"],
            "mode": MODES.get(route_type, "other"),
        }

    # service_id -> (jede ve vsedni den, jede v sobotu)
    service_days = {}
    for row in _read_csv(zip_file, "calendar.txt"):
        service_days[row["service_id"]] = (
            row.get("wednesday") == "1",
            row.get("saturday") == "1",
        )

    trip_key = {}
    trip_service = {}
    for row in _read_csv(zip_file, "trips.txt"):
        if row["route_id"] in routes:
            trip_id = row["trip_id"]
            trip_key[trip_id] = (row["route_id"], row.get("direction_id") or "0")
            trip_service[trip_id] = row.get("service_id")

    representative = {}
    sequences = {}
    first_departure = {}
    for row in _read_csv(zip_file, "stop_times.txt"):
        trip_id = row["trip_id"]
        key = trip_key.get(trip_id)
        if key is None or _is_technical_stop(row["stop_id"]):
            continue

        sequence = int(row["stop_sequence"])
        known = first_departure.get(trip_id)
        if known is None or sequence < known[0]:
            first_departure[trip_id] = (sequence, _parse_gtfs_time(row["departure_time"] or row["arrival_time"]))

        if representative.setdefault(key, trip_id) != trip_id:
            continue
        sequences.setdefault(trip_id, []).append(
            (sequence, row["stop_id"], _parse_gtfs_time(row["arrival_time"]))
        )

    # intervaly: odjezdy vsech spoju linky+smeru z vychozi zastavky podle typu dne
    departures = defaultdict(lambda: ([], []))
    for trip_id, (_seq, dep_min) in first_departure.items():
        days = service_days.get(trip_service.get(trip_id))
        if days is None:
            continue
        weekday_deps, weekend_deps = departures[trip_key[trip_id]]
        if days[0]:
            weekday_deps.append(dep_min)
        if days[1]:
            weekend_deps.append(dep_min)

    # Krome intervalu se uklada i pocet spoju: nula znamena "v tento typ dne
    # nejede" (linku je treba pri hledani preskocit), jednicka "interval nelze
    # spocitat" (dostane strop cekani).
    headways = {}
    for key, (weekday_deps, weekend_deps) in departures.items():
        headways["|".join(key)] = [
            _median_headway(weekday_deps),
            _median_headway(weekend_deps),
            len(weekday_deps),
            len(weekend_deps),
        ]

    used_stops = set()
    edges = []
    for (route_id, direction), trip_id in representative.items():
        stops_in_trip = sorted(sequences.get(trip_id, []))
        route = routes[route_id]
        line_key = f"{route_id}|{direction}"
        for (_, stop_a, time_a), (_, stop_b, time_b) in zip(stops_in_trip, stops_in_trip[1:]):
            minutes = max(time_b - time_a, 0.5)
            edges.append([stop_a, stop_b, round(minutes, 2), route["name"], route["mode"], line_key])
            used_stops.update((stop_a, stop_b))

    stops = {}
    for row in _read_csv(zip_file, "stops.txt"):
        if row["stop_id"] in used_stops:
            stops[row["stop_id"]] = [
                row["stop_name"],
                round(float(row["stop_lat"]), 6),
                round(float(row["stop_lon"]), 6),
            ]

    graph = {"version": GRAPH_VERSION, "stops": stops, "edges": edges, "headways": headways}
    GRAPH_CACHE.write_text(json.dumps(graph), encoding="utf-8")
    return graph


def load_transit_graph():
    if GRAPH_CACHE.exists():
        graph = json.loads(GRAPH_CACHE.read_text(encoding="utf-8"))
        if graph.get("version") == GRAPH_VERSION:
            return graph
    if not GTFS_ZIP.exists():
        download_gtfs()
    return build_transit_graph()


class TransitNetwork:
    def __init__(self, graph):
        self.stops = graph["stops"]
        self.headways = graph.get("headways", {})
        self.adjacency = defaultdict(list)
        for stop_a, stop_b, minutes, line, mode, line_key in graph["edges"]:
            self.adjacency[stop_a].append((stop_b, minutes, line, mode, line_key))

        # prestupni vazby: zastavky stejneho jmena nebo do TRANSFER_WALK_MAX_M
        self.transfers = defaultdict(set)
        by_name = defaultdict(list)
        cell = {}
        for stop_id, (name, lat, lon) in self.stops.items():
            by_name[name].append(stop_id)
            cell.setdefault((round(lat / 0.003), round(lon / 0.005)), []).append(stop_id)
        for group in by_name.values():
            for a in group:
                _, lat_a, lon_a = self.stops[a]
                for b in group:
                    if b == a:
                        continue
                    _, lat_b, lon_b = self.stops[b]
                    if _haversine_m(lat_a, lon_a, lat_b, lon_b) <= NAME_TRANSFER_MAX_M:
                        self.transfers[a].add(b)
        for (cy, cx), members in cell.items():
            nearby = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nearby.extend(cell.get((cy + dy, cx + dx), []))
            for a in members:
                name_a, lat_a, lon_a = self.stops[a]
                for b in nearby:
                    if b == a:
                        continue
                    _, lat_b, lon_b = self.stops[b]
                    if _haversine_m(lat_a, lon_a, lat_b, lon_b) <= TRANSFER_WALK_MAX_M:
                        self.transfers[a].add(b)

    def stops_near(self, lat, lon, radius_m):
        found = []
        for stop_id, (name, stop_lat, stop_lon) in self.stops.items():
            distance = _haversine_m(lat, lon, stop_lat, stop_lon)
            if distance <= radius_m:
                found.append((distance, stop_id))
        return sorted(found)

    def line_runs(self, line_key, day="weekday"):
        """Jezdi linka v dany typ dne? (neznama linka se nefiltruje)"""
        entry = self.headways.get(line_key)
        if not entry or len(entry) < 4:
            return True
        return bool(entry[2 if day == "weekday" else 3])

    def wait_min(self, line_key, mode, day="weekday"):
        """Ocekavane cekani = polovina intervalu linky pro dany typ dne."""
        entry = self.headways.get(line_key)
        if not entry:
            return MODE_WAIT_MIN.get(mode, 6.0)

        value = entry[0 if day == "weekday" else 1]
        if value is None:
            # jediny spoj za den - interval neexistuje
            return SPARSE_LINE_WAIT_MIN
        low, high = WAIT_MIN_CLAMP
        return min(max(value / 2, low), high)

    def route(self, origin_stops, target_stops, day="weekday",
              origin_costs=None, target_costs=None):
        """Nejlepsi spojeni z mnoziny zastavek do mnoziny zastavek.

        Cena = prestupy * TRANSFER_PENALTY_MIN + jizda * MODE_MULTIPLIER + cekani
        (interval/2 dle typu dne). origin_costs/target_costs pridavaji cenu
        dobehu na nastupni a z vystupni zastavky (v minutach behu), takze se
        vybere zastavka vyhodna z pohledu CELE vypravy, ne jen jizdy samotne;
        do casu jizdy (`minutes`) se nepocitaji - ty si vyprava vede zvlast.

        Stav dijkstry je (zastavka, linka, prijel-jsem-sem), aby sly prestupy
        pocitat presne a aby se z cile vystupovalo tam, kam skutecne dojela
        linka (ne po pesim prestupu jinam). Vraci {minutes, cost, transfers,
        legs, stop_id} nebo None.
        """
        targets = set(target_stops)
        origin_costs = origin_costs or {}
        target_costs = target_costs or {}

        counter = itertools.count()  # stabilni poradi, heap nikdy neporovnava data
        heap = []
        for stop in origin_stops:
            heapq.heappush(heap, (origin_costs.get(stop, 0.0), next(counter),
                                  0.0, 0, stop, None, (), None))
        best = {}

        while heap:
            cost, _, minutes, transfers, stop, line, path, arrived = heapq.heappop(heap)

            if stop is _GOAL:
                legs = self._compress_legs(path)
                return {
                    "minutes": round(minutes, 1),
                    "cost": round(cost, 1),
                    "transfers": transfers,
                    "legs": legs,
                    "stop_id": legs[-1]["to_id"] if legs else arrived,
                }

            state = (stop, line, arrived == stop)
            if state in best and best[state] <= cost:
                continue
            best[state] = cost

            # Vystup do cile jen tam, kam me dovezla linka (nebo z vychozi
            # zastavky) - jinak by itinerar koncil jinde, nez zacina beh.
            if stop in targets and arrived in (None, stop):
                heapq.heappush(heap, (cost + target_costs.get(stop, 0.0), next(counter),
                                      minutes, transfers, _GOAL, line, path, stop))

            for next_stop, ride_min, next_line, mode, line_key in self.adjacency[stop]:
                if not self.line_runs(line_key, day):
                    continue
                boarding = line != next_line
                extra_transfers = 1 if boarding and line is not None else 0
                extra_cost = ride_min * MODE_MULTIPLIER.get(mode, 1.6)
                extra_minutes = ride_min
                wait = 0.0
                if boarding:
                    wait = self.wait_min(line_key, mode, day)
                    extra_cost += wait + extra_transfers * TRANSFER_PENALTY_MIN
                    extra_minutes += wait
                heapq.heappush(heap, (
                    cost + extra_cost,
                    next(counter),
                    minutes + extra_minutes,
                    transfers + extra_transfers,
                    next_stop,
                    next_line,
                    path + ((next_line, mode, stop, next_stop, ride_min, wait),),
                    next_stop,
                ))

            for other in self.transfers[stop]:
                heapq.heappush(heap, (cost, next(counter), minutes, transfers,
                                      other, line, path, arrived))

        return None

    def _compress_legs(self, path):
        legs = []
        for line, mode, stop_from, stop_to, ride_min, wait in path:
            to_lat, to_lon = self.stops[stop_to][1:3]
            if legs and legs[-1]["line"] == line:
                legs[-1]["to"] = self.stops[stop_to][0]
                legs[-1]["to_id"] = stop_to
                legs[-1]["to_lat"], legs[-1]["to_lon"] = to_lat, to_lon
                legs[-1]["stops"] += 1
                legs[-1]["minutes"] = round(legs[-1]["minutes"] + ride_min, 1)
                legs[-1]["coords"].append([to_lat, to_lon])
            else:
                legs.append({
                    "line": line,
                    "mode": mode,
                    "wait_min": round(wait, 1),
                    "from": self.stops[stop_from][0],
                    "from_id": stop_from,
                    "from_lat": self.stops[stop_from][1],
                    "from_lon": self.stops[stop_from][2],
                    "to": self.stops[stop_to][0],
                    "to_id": stop_to,
                    "to_lat": to_lat,
                    "to_lon": to_lon,
                    "stops": 1,
                    "minutes": round(ride_min + wait, 1),
                    "coords": [
                        [self.stops[stop_from][1], self.stops[stop_from][2]],
                        [to_lat, to_lon],
                    ],
                })
        return legs
