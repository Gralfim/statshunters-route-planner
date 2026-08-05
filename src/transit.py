"""MHD sit z PID GTFS: kompaktni graf zastavek + router.

Router minimalizuje primarne pocet prestupu, sekundarne cas vazeny prioritou
dopravnich prostredku (metro > tramvaj > vlak > ostatni). Casy jizdy pochazeji
z jizdnich radu (reprezentativni spoj kazde linky a smeru), cekani na spoj je
polovina intervalu linky pro dany typ dne (vsedni den / vikend, z GTFS
calendar); kde interval neni znamy, pouzije se pausal podle druhu dopravy.

Cely jizdni rad se sklada pro JEDNU konkretni stredu a JEDNU sobotu - viz
REFERENCE_WEEKDAY. Feed se pritom hlida na expiraci (viz refresh_gtfs).
"""
import csv
import datetime
import heapq
import io
import itertools
import json
import math
import statistics
import sys
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

_GOAL = object()  # virtualni cilovy uzel (viz TransitNetwork.route)

ROOT = Path(__file__).resolve().parents[1]
GTFS_ZIP = ROOT / "data" / "pid_gtfs.zip"
GRAPH_CACHE = ROOT / "data" / "transit_graph.json"
GTFS_URL = "https://data.pid.cz/PID_GTFS.zip"
GRAPH_VERSION = 5

# Jizdni rad se stavi na KONKRETNI referencni dny, ne na "vsechny stredy, ktere
# v datech jsou". PID vede soubezne nekolik variant teze linky (bezny provoz,
# vyluka, prazdniny) a kazda ma vlastni service_id s vlastni platnosti. Kdyz se
# ctou jen priznaky dnu v calendar.txt a platnost se ignoruje, spoje ze vsech
# obdobi se sectou dohromady:
#   * u S6 tim vysel interval 2,5 minuty misto 30 - tyz vlak je v datech
#     dvakrat (bezna varianta a vylukova) a lisi se o 2 minuty, takze median
#     rozestupu sahne po techto dvouminutovych parech misto po skutecnych 30;
#   * jako reprezentativni spoj se vybrala predvylukova varianta ze Smichova,
#     ackoli od 7. 7. 2026 jezdi ze Zlichova.
# Zmereno na feedu 18.-31. 7. 2026: ze 1517 kombinaci linka+smer bylo takto
# zkresleno 14 (S6 nejvic, pak 191: 3 vs 12 min a 172: 5 vs 30 min). Ostatni se
# zachranily nahodou - jejich duplicitni varianty maji shodne casy a nulove
# rozestupy filtr v _median_headway zahodi.
REFERENCE_WEEKDAY = 2   # streda (datetime.date.weekday())
REFERENCE_WEEKEND = 5   # sobota

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


def _date(text):
    return datetime.date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def feed_window(gtfs_zip=GTFS_ZIP):
    """(zacatek, konec) platnosti feedu, nebo None. PID vydava jizdni rad na
    par tydnu dopredu - po konci platnosti uz nepopisuje skutecny provoz."""
    try:
        with zipfile.ZipFile(gtfs_zip) as zip_file:
            for row in _read_csv(zip_file, "feed_info.txt"):
                start, end = row.get("feed_start_date"), row.get("feed_end_date")
                if start and end:
                    return _date(start), _date(end)
    except Exception:
        pass
    return None


def refresh_gtfs(today=None):
    """Stahne GTFS, kdyz chybi nebo uz mu skoncila platnost.

    Drive se stahovalo jen kdyz soubor vubec neexistoval, takze planovac jel
    na libovolne starem jizdnim radu - namereno pul mesice po konci platnosti,
    tedy i s vylukou, ktera se mezitim zmenila. Kdyz se stazeni nepovede a nejaky
    feed uz mame, jede se dal na nem, ale s varovanim.
    """
    today = today or datetime.date.today()
    if GTFS_ZIP.exists():
        window = feed_window()
        if window is None or today <= window[1]:
            return
        stale = f"platnost skoncila {window[1].isoformat()}"
    else:
        stale = "chybi"
    try:
        download_gtfs()
    except Exception as error:
        if not GTFS_ZIP.exists():
            raise
        print(f"VAROVANI: PID GTFS se nepodarilo aktualizovat ({error}) - jede se "
              f"na starem jizdnim radu ({stale})", file=sys.stderr)


def _reference_dates(zip_file, today=None):
    """Konkretni streda a sobota, na kterych jizdni rad stoji.

    Bere nejblizsi takovy den ode dneska. Kdyz uz je za koncem platnosti feedu
    (aktualizace se nepovedla), posledni takovy uvnitr platnosti - stary rad je
    porad lepsi nez zadny."""
    today = today or datetime.date.today()
    window = None
    for row in _read_csv(zip_file, "feed_info.txt"):
        if row.get("feed_start_date") and row.get("feed_end_date"):
            window = (_date(row["feed_start_date"]), _date(row["feed_end_date"]))
            break

    dates = []
    for weekday in (REFERENCE_WEEKDAY, REFERENCE_WEEKEND):
        day = today + datetime.timedelta(days=(weekday - today.weekday()) % 7)
        if window and day > window[1]:
            last = window[1]
            day = last - datetime.timedelta(days=(last.weekday() - weekday) % 7)
        dates.append(day)
    return tuple(dates)


WEEKDAY_COLUMNS = ("monday", "tuesday", "wednesday", "thursday", "friday",
                   "saturday", "sunday")


def _active_services(zip_file, dates):
    """service_id -> (jede v prvni referencni den, jede v druhy).

    Krome priznaku dne se ctou i meze platnosti a vyjimky z calendar_dates.txt
    (1 = spoj navic v tento den, 2 = odrekly) - bez nich se michaji varianty
    z ruznych obdobi."""
    keys = [day.strftime("%Y%m%d") for day in dates]
    active = {}
    for row in _read_csv(zip_file, "calendar.txt"):
        start, end = row["start_date"], row["end_date"]
        active[row["service_id"]] = tuple(
            row.get(WEEKDAY_COLUMNS[day.weekday()]) == "1" and start <= key <= end
            for day, key in zip(dates, keys)
        )
    for row in _read_csv(zip_file, "calendar_dates.txt"):
        if row["date"] not in keys:
            continue
        # sluzba muze byt jen v calendar_dates.txt (jednorazovy provoz)
        days = list(active.get(row["service_id"], (False,) * len(dates)))
        days[keys.index(row["date"])] = row["exception_type"] == "1"
        active[row["service_id"]] = tuple(days)
    return active


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


def build_transit_graph(gtfs_zip=GTFS_ZIP, today=None):
    """Z GTFS postavi kompaktni graf: zastavky + hrany po sobe jdoucich zastavek
    reprezentativniho spoje kazde (linka, smer) + intervaly linek pro vsedni den
    a vikend (median rozestupu odjezdu z vychozi zastavky). Cachuje do JSON.

    Vsechno se bere z jednoho konkretniho vsedniho dne a jedne soboty
    (viz REFERENCE_WEEKDAY) - varianty linek platne v jinem obdobi se ignoruji."""
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

    dates = _reference_dates(zip_file, today)
    active = _active_services(zip_file, dates)

    trip_key = {}
    trip_days = {}
    for row in _read_csv(zip_file, "trips.txt"):
        if row["route_id"] not in routes:
            continue
        days = active.get(row.get("service_id"))
        if not days or not any(days):
            continue    # varianta pro jine obdobi - do dnesniho radu nepatri
        trip_key[row["trip_id"]] = (row["route_id"], row.get("direction_id") or "0")
        trip_days[row["trip_id"]] = days

    # Prvni pruchod: odjezd z vychozi zastavky (pro intervaly) a pocet zastavek
    # (pro vyber reprezentanta).
    first_departure = {}
    stop_count = Counter()
    for row in _read_csv(zip_file, "stop_times.txt"):
        trip_id = row["trip_id"]
        if trip_id not in trip_key or _is_technical_stop(row["stop_id"]):
            continue
        stop_count[trip_id] += 1
        sequence = int(row["stop_sequence"])
        known = first_departure.get(trip_id)
        if known is None or sequence < known[0]:
            first_departure[trip_id] = (
                sequence, _parse_gtfs_time(row["departure_time"] or row["arrival_time"]))

    # Reprezentativni spoj urcuje, ktere zastavky linka obsluhuje - vybira se
    # nejdelsi z platnych, a prednost ma vsedni den. Drive to byl ten, na ktery
    # se narazilo prvni: mohla to byt varianta z jineho obdobi (odtud "vlak ze
    # Smichova" behem vyluky) nebo zkraceny spoj, kteremu chybi pulka zastavek.
    best = {}
    for trip_id, key in trip_key.items():
        rank = (trip_days[trip_id][0], stop_count[trip_id])
        if key not in best or rank > best[key][0]:
            best[key] = (rank, trip_id)
    representative = {key: trip_id for key, (_rank, trip_id) in best.items()}

    wanted = set(representative.values())
    sequences = defaultdict(list)
    for row in _read_csv(zip_file, "stop_times.txt"):
        trip_id = row["trip_id"]
        if trip_id in wanted and not _is_technical_stop(row["stop_id"]):
            sequences[trip_id].append(
                (int(row["stop_sequence"]), row["stop_id"], _parse_gtfs_time(row["arrival_time"]))
            )

    # intervaly: odjezdy vsech spoju linky+smeru z vychozi zastavky podle typu dne
    departures = defaultdict(lambda: ([], []))
    for trip_id, (_seq, dep_min) in first_departure.items():
        weekday_deps, weekend_deps = departures[trip_key[trip_id]]
        days = trip_days[trip_id]
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

    graph = {"version": GRAPH_VERSION, "stops": stops, "edges": edges,
             "headways": headways, "feed": _feed_identity(gtfs_zip),
             "built_for": [day.isoformat() for day in dates]}
    GRAPH_CACHE.write_text(json.dumps(graph), encoding="utf-8")
    return graph


def _feed_identity(gtfs_zip=GTFS_ZIP):
    """Cim se pozna, ze na disku lezi jiny feed nez ten, ze ktereho je graf.
    Velikost souboru staci - PID vydava kazdy feed zvlast."""
    window = feed_window(gtfs_zip)
    return [window[0].isoformat(), window[1].isoformat(),
            Path(gtfs_zip).stat().st_size] if window else None


def _graph_matches_feed(graph, gtfs_zip=GTFS_ZIP, today=None):
    """Graf plati, dokud stoji na feedu, ktery lezi na disku.

    Referencni dny se navic musi hodit na dnesek - postaveny "na pristi stredu"
    zestarne, jakmile ta streda projde. U prosleho feedu to neplati: tam uz jsou
    referencni dny pritlacene dovnitr stare platnosti a prestavba by nic
    nezmenila, jen by bezela pri kazdem volani."""
    if graph.get("feed") != _feed_identity(gtfs_zip):
        return False
    today = today or datetime.date.today()
    window = feed_window(gtfs_zip)
    if window and today > window[1]:
        return True
    built = graph.get("built_for") or []
    return bool(built) and all(datetime.date.fromisoformat(day) >= today for day in built)


def load_transit_graph(today=None):
    today = today or datetime.date.today()
    refresh_gtfs(today)
    if GRAPH_CACHE.exists() and GTFS_ZIP.exists():
        try:
            graph = json.loads(GRAPH_CACHE.read_text(encoding="utf-8"))
        except Exception:
            graph = {}
        if graph.get("version") == GRAPH_VERSION and _graph_matches_feed(graph, today=today):
            return graph
    if not GTFS_ZIP.exists():
        download_gtfs()
    return build_transit_graph(today=today)


# Barvy prazskeho metra - v mape maji byt ty, ktere ma clovek spojene s linkou,
# ne nase paleta obdobi.
METRO_COLORS = {"A": "#0aa04b", "B": "#f0ab00", "C": "#d9232e"}


def metro_geometry(graph):
    """Trasy metra a stanice pro podklad v mape.

    Turisticka vrstva Mapy.cz metro nekresli, ale sit PID uz mame nactenou kvuli
    vypravam - staci ji prevest na linie. Kazda stanice ma v GTFS vic uzlu
    (nastupiste pro kazdy smer), takze se stanice slucuji podle NAZVU; jinak by
    kazda linka vysla jako dve rovnobezky par metru od sebe.
    """
    stops = graph["stops"]
    edges = [edge for edge in graph["edges"] if edge[4] == "metro"]

    positions = defaultdict(list)
    lines_at = defaultdict(set)
    for from_id, to_id, _minutes, line, _mode, _run in edges:
        for stop_id in (from_id, to_id):
            name, lat, lon = stops[stop_id]
            positions[name].append((lat, lon))
            lines_at[name].add(line)

    station = {
        name: (round(sum(p[0] for p in points) / len(points), 6),
               round(sum(p[1] for p in points) / len(points), 6))
        for name, points in positions.items()
    }

    segments = defaultdict(set)
    for from_id, to_id, _minutes, line, _mode, _run in edges:
        a, b = stops[from_id][0], stops[to_id][0]
        if a != b:  # oba smery daji tentyz usek
            segments[line].add((a, b) if a <= b else (b, a))

    return {
        "lines": [
            {
                "line": line,
                "color": METRO_COLORS.get(line, "#53606f"),
                "segments": [[station[a], station[b]] for a, b in sorted(pairs)],
            }
            for line, pairs in sorted(segments.items())
        ],
        "stations": [
            {"name": name, "lat": station[name][0], "lon": station[name][1],
             "lines": sorted(lines_at[name])}
            for name in sorted(station)
        ],
    }


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
