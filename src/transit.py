"""MHD sit z PID GTFS: kompaktni graf zastavek + router.

Router minimalizuje primarne pocet prestupu, sekundarne cas vazeny prioritou
dopravnich prostredku (metro > tramvaj > vlak > ostatni). Casy jizdy pochazeji
z jizdnich radu (reprezentativni spoj kazde linky a smeru), cekani na spoj je
pausalni podle druhu dopravy.
"""
import csv
import heapq
import io
import json
import math
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GTFS_ZIP = ROOT / "data" / "pid_gtfs.zip"
GRAPH_CACHE = ROOT / "data" / "transit_graph.json"
GTFS_URL = "https://data.pid.cz/PID_GTFS.zip"

# GTFS route_type -> nas druh dopravy
MODES = {0: "tram", 1: "metro", 2: "train", 3: "bus"}

# priorita druhu: nasobic casu jizdy (mensi = preferovany)
MODE_MULTIPLIER = {"metro": 1.0, "tram": 1.15, "train": 1.25, "bus": 1.5, "other": 1.6}
# pausalni cekani pri nastupu/prestupu (minuty)
MODE_WAIT_MIN = {"metro": 2.0, "tram": 4.0, "train": 8.0, "bus": 6.0, "other": 6.0}
TRANSFER_PENALTY_MIN = 30.0  # velka vaha = nejdriv minimalizuj prestupy
TRANSFER_WALK_MAX_M = 250.0

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


def build_transit_graph(gtfs_zip=GTFS_ZIP):
    """Z GTFS postavi kompaktni graf: zastavky + hrany po sobe jdoucich zastavek
    reprezentativniho spoje kazde (linka, smer). Vysledek cachuje do JSON."""
    zip_file = zipfile.ZipFile(gtfs_zip)

    routes = {}
    for row in _read_csv(zip_file, "routes.txt"):
        route_type = int(row.get("route_type") or 3)
        routes[row["route_id"]] = {
            "name": row.get("route_short_name") or row["route_id"],
            "mode": MODES.get(route_type, "other"),
        }

    trip_key = {}
    for row in _read_csv(zip_file, "trips.txt"):
        if row["route_id"] in routes:
            trip_key[row["trip_id"]] = (row["route_id"], row.get("direction_id") or "0")

    representative = {}
    for row in _read_csv(zip_file, "stop_times.txt"):
        key = trip_key.get(row["trip_id"])
        if key is None:
            continue
        chosen = representative.setdefault(key, row["trip_id"])
        if chosen != row["trip_id"]:
            continue
        representative.setdefault("_sequences", {}).setdefault(row["trip_id"], []).append(
            (int(row["stop_sequence"]), row["stop_id"], _parse_gtfs_time(row["arrival_time"]))
        )

    sequences = representative.pop("_sequences", {})

    used_stops = set()
    edges = []
    for (route_id, _direction), trip_id in representative.items():
        stops_in_trip = sorted(sequences.get(trip_id, []))
        route = routes[route_id]
        for (_, stop_a, time_a), (_, stop_b, time_b) in zip(stops_in_trip, stops_in_trip[1:]):
            minutes = max(time_b - time_a, 0.5)
            edges.append([stop_a, stop_b, round(minutes, 2), route["name"], route["mode"]])
            used_stops.update((stop_a, stop_b))

    stops = {}
    for row in _read_csv(zip_file, "stops.txt"):
        if row["stop_id"] in used_stops:
            stops[row["stop_id"]] = [
                row["stop_name"],
                round(float(row["stop_lat"]), 6),
                round(float(row["stop_lon"]), 6),
            ]

    graph = {"stops": stops, "edges": edges}
    GRAPH_CACHE.write_text(json.dumps(graph), encoding="utf-8")
    return graph


def load_transit_graph():
    if GRAPH_CACHE.exists():
        return json.loads(GRAPH_CACHE.read_text(encoding="utf-8"))
    if not GTFS_ZIP.exists():
        download_gtfs()
    return build_transit_graph()


class TransitNetwork:
    def __init__(self, graph):
        self.stops = graph["stops"]
        self.adjacency = defaultdict(list)
        for stop_a, stop_b, minutes, line, mode in graph["edges"]:
            self.adjacency[stop_a].append((stop_b, minutes, line, mode))

        # prestupni vazby: zastavky stejneho jmena nebo do TRANSFER_WALK_MAX_M
        self.transfers = defaultdict(set)
        by_name = defaultdict(list)
        cell = {}
        for stop_id, (name, lat, lon) in self.stops.items():
            by_name[name].append(stop_id)
            cell.setdefault((round(lat / 0.003), round(lon / 0.005)), []).append(stop_id)
        for group in by_name.values():
            for a in group:
                self.transfers[a].update(b for b in group if b != a)
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

    def route(self, origin_stops, target_stops):
        """Nejlepsi spojeni z mnoziny zastavek do mnoziny zastavek.

        Cena = prestupy * TRANSFER_PENALTY_MIN + jizda * MODE_MULTIPLIER + cekani.
        Stav dijkstry je (zastavka, linka), aby sly prestupy pocitat presne.
        Vraci {minutes, cost, transfers, legs} nebo None.
        """
        targets = set(target_stops)
        heap = []
        for stop in origin_stops:
            heapq.heappush(heap, (0.0, 0.0, 0, stop, None, ()))
        best = {}

        while heap:
            cost, minutes, transfers, stop, line, path = heapq.heappop(heap)
            state = (stop, line)
            if state in best and best[state] <= cost:
                continue
            best[state] = cost

            if stop in targets:
                return {
                    "minutes": round(minutes, 1),
                    "cost": round(cost, 1),
                    "transfers": transfers,
                    "legs": self._compress_legs(path),
                    "stop_id": stop,
                }

            for next_stop, ride_min, next_line, mode in self.adjacency[stop]:
                boarding = line != next_line
                extra_transfers = 1 if boarding and line is not None else 0
                extra_cost = ride_min * MODE_MULTIPLIER.get(mode, 1.6)
                extra_minutes = ride_min
                if boarding:
                    extra_cost += MODE_WAIT_MIN.get(mode, 6.0) + extra_transfers * TRANSFER_PENALTY_MIN
                    extra_minutes += MODE_WAIT_MIN.get(mode, 6.0)
                heapq.heappush(heap, (
                    cost + extra_cost,
                    minutes + extra_minutes,
                    transfers + extra_transfers,
                    next_stop,
                    next_line,
                    path + ((next_line, mode, stop, next_stop, ride_min),),
                ))

            for other in self.transfers[stop]:
                heapq.heappush(heap, (cost, minutes, transfers, other, line, path))

        return None

    def _compress_legs(self, path):
        legs = []
        for line, mode, stop_from, stop_to, ride_min in path:
            if legs and legs[-1]["line"] == line:
                legs[-1]["to"] = self.stops[stop_to][0]
                legs[-1]["to_id"] = stop_to
                legs[-1]["to_lat"], legs[-1]["to_lon"] = self.stops[stop_to][1:3]
                legs[-1]["stops"] += 1
                legs[-1]["minutes"] = round(legs[-1]["minutes"] + ride_min, 1)
            else:
                legs.append({
                    "line": line,
                    "mode": mode,
                    "from": self.stops[stop_from][0],
                    "from_id": stop_from,
                    "from_lat": self.stops[stop_from][1],
                    "from_lon": self.stops[stop_from][2],
                    "to": self.stops[stop_to][0],
                    "to_id": stop_to,
                    "to_lat": self.stops[stop_to][1],
                    "to_lon": self.stops[stop_to][2],
                    "stops": 1,
                    "minutes": round(ride_min + MODE_WAIT_MIN.get(mode, 6.0), 1),
                })
        return legs
