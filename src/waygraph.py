"""Pesi graf OSM: stazeni, cache, obohaceni a prostorove indexy.

Tri vrstvy cache nad sebou:

1. `data/walk_*.graphml` - surovy graf z Overpass. Cache je podle POKRYTI:
   pouzije se jakykoli ulozeny graf, jehoz kruh pokryva pozadovany start a
   dosah, takze zmena delky ani startu v okoli nevyvola nove stahovani.
2. `data/walk_*.prepared-*.pkl` - tentyz graf uz PRIPRAVENY (nazvy ulic, znacene
   trasy, ceny hran). Parsovani graphml trva ~22 s a priprava dalsich ~6 s;
   z pickle je to ~4 s. V nazvu je otisk parametru, ktere pripravu ovlivnuji -
   po zmene preferenci se stary soubor nepouzije.
3. `_GRAPH_MEMORY` - nacteny graf zustava v pameti procesu.
"""
import math
import pickle
from pathlib import Path

from geo import bearing, haversine_m, tag
from runcost import NAMEABLE_HIGHWAYS, cost_parameters, prepare_run_costs

ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = ROOT / "data"

MIN_DOWNLOAD_REACH_KM = 10
# Reach je horni odhad (vzdusnou carou max/2 + rezerva); kandidatni tiles lezi
# nejvyse na 90 % teto vzdalenosti. Chybejici vnejsi lem grafu tedy trasu
# nerozbije, jen ji v krajnim pripade lehce zhorsi - a usetri minutove stahovani
# temer identicke oblasti z Overpass.
COVERAGE_SLACK_KM = 1.5

STREET_MATCH_MAX_M = 40.0   # chodnik "podel ulice" - max odstup od pojmenovane cesty
STREET_MATCH_MAX_DEG = 35.0  # ... a max odchylka smeru (aby slo o soubeznou cestu)
# Geometrie relace znacene trasy byva zjednodusena, takze se od cesty odchyluje
# i o par desitek metru (mereno: cervena turisticka 24 m od pesiny, po ktere vede).
TRAIL_MATCH_MAX_M = 35.0

# Zvys, kdyz se zmeni SAMA LOGIKA pripravy (novy atribut, jiny zpusob parovani).
# Zmenu parametru nize hlida otisk sam.
PREPARED_CACHE_VERSION = 2  # 2: cyklotrasy bez znaceni v terenu se zahazuji


def graph_path(lat, lon, reach_km):
    return GRAPH_DIR / f"walk_{lat:.3f}_{lon:.3f}_{reach_km:.1f}km.graphml"


def covering_graph_path(lat, lon, reach_km):
    """Najdi ulozeny graf, jehoz oblast pokryva pozadovany kruh (start, reach)."""
    best = None
    for path in GRAPH_DIR.glob("walk_*km.graphml"):
        try:
            _, cached_lat, cached_lon, cached_reach = path.stem.split("_")
            cached_lat, cached_lon = float(cached_lat), float(cached_lon)
            cached_reach_km = float(cached_reach.removesuffix("km"))
        except ValueError:
            continue
        distance_km = haversine_m(lat, lon, cached_lat, cached_lon) / 1000
        if distance_km + reach_km <= cached_reach_km + COVERAGE_SLACK_KM:
            if best is None or cached_reach_km < best[1]:
                best = (path, cached_reach_km)
    return best[0] if best else None


def _match_parallel(graph, edges_data, mids, owns, segments, max_m, attribute,
                    candidates=6, extra=None):
    """Priradi hranam nejblizsi soubeznou linii (ulici/znacenou trasu).
    candidates = kolik nejblizsich segmentu se zkusi; v mistech s vice trasami
    pres sebe (cyklotrasy) jich par nestaci, nez se najde soubezny.
    extra = (pole hodnot, jmeno atributu) - druhy udaj prevzaty od TEHOZ
    nalezeneho segmentu (u ulic priznak, ze je vyznamna)."""
    import numpy as np
    from scipy.spatial import cKDTree

    slats, slons, sbear, slabels = segments[:4]
    if not len(slabels) or not edges_data:
        return

    # Jedna referencni projekce pro strom i dotazy. Pri lon ~14.4 znamena i maly
    # rozdil coslat posun radove kilometry, takze per-bod coslat by hledani
    # nejblizsich uplne rozbil.
    coslat = math.cos(math.radians(float(slats.mean())))
    tree = cKDTree(np.column_stack((slons * 111320.0 * coslat, slats * 111320.0)))
    mids = np.asarray(mids)
    query_points = np.column_stack((mids[:, 1] * 111320.0 * coslat, mids[:, 0] * 111320.0))
    k = min(candidates, len(slabels))
    dists, idxs = tree.query(query_points, k=k)
    dists = np.atleast_2d(dists.T).T
    idxs = np.atleast_2d(idxs.T).T

    for i, data in enumerate(edges_data):
        for j in range(k):
            if dists[i, j] > max_m:
                break
            diff = abs(sbear[idxs[i, j]] - owns[i])
            diff = min(diff, 180 - diff)
            if diff <= STREET_MATCH_MAX_DEG:
                data[attribute] = str(slabels[idxs[i, j]])
                if extra is not None:
                    values, extra_attribute = extra
                    data[extra_attribute] = bool(values[idxs[i, j]])
                break


def enrich_streets(graph, lat, lon, reach_km):
    """Chodnikum/stezkam bez jmena priradi ulici, podel ktere vedou (atributy
    along_street a along_major), vsem cestam znacenou trasu (atribut trail), a
    ulozi index pojmenovanych ulic na graf. Osy ulic v pesim grafu casto chybi
    (Ke Karlovu 0 hran), znacene trasy jsou v OSM relace - oboji z externiho
    zdroje. Bezi jednou per graf (graf se drzi v pameti)."""
    import landmarks

    segments = landmarks.street_segments(landmarks.load_streets(lat, lon, reach_km))
    graph.graph["street_segments"] = segments

    unnamed, unnamed_mids, unnamed_owns = [], [], []
    walkable, walkable_mids, walkable_owns = [], [], []
    for u, v, data in graph.edges(data=True):
        highway = tag(data, "highway")
        mlat = (graph.nodes[u]["y"] + graph.nodes[v]["y"]) / 2
        mlon = (graph.nodes[u]["x"] + graph.nodes[v]["x"]) / 2
        own = bearing(graph, u, v) % 180
        point = (mlat, mlon)  # projekci resi _match_parallel jednotne

        if not data.get("name") and highway in NAMEABLE_HIGHWAYS:
            unnamed.append(data)
            unnamed_mids.append(point)
            unnamed_owns.append(own)
        # znacena trasa muze vest i po pojmenovane ceste
        walkable.append(data)
        walkable_mids.append(point)
        walkable_owns.append(own)

    # segments[4] = priznak vyznamne ulice (tertiary+); bere se od tehoz
    # segmentu jako nazev, ne podle jmena ulice - tataz ulice muze byt jinde
    # klidna a jinde hlavni tah.
    _match_parallel(graph, unnamed, unnamed_mids, unnamed_owns,
                    segments, STREET_MATCH_MAX_M, "along_street",
                    extra=(segments[4], "along_major"))

    trails = landmarks.load_trails(lat, lon, reach_km)
    if not trails:
        # zadne znacene trasy = bud jich tu neni, nebo zdroj vypadl; graf se pak
        # necachuje (viz _prepare), aby se degradace nezafixovala
        graph.graph["sources_complete"] = False
    else:
        _match_parallel(graph, walkable, walkable_mids, walkable_owns,
                        landmarks.line_segments(trails), TRAIL_MATCH_MAX_M, "trail",
                        candidates=20)
    return graph


def _prepare(graph, lat, lon, reach_km):
    """Doplni grafu kontext a ceny hran. Vraci (graf, uplny?).

    Kdyz nektery zdroj vypadne, planovani pokracuje - jen z typu cesty. Takovy
    graf se ale NESMI ulozit do cache: jedno 504 od Overpassu by tim pripravilo
    oblast o znacene trasy natrvalo a menilo i navrhovane trasy (znacka zlevnuje
    hrany). Radeji pomala priprava pokazde nez tise horsi vysledky.
    """
    complete = True
    try:
        enrich_streets(graph, lat, lon, reach_km)
    except Exception:
        graph.graph.setdefault("street_segments", None)
        complete = False
    if not graph.graph.get("sources_complete", True):
        complete = False
    prepare_run_costs(graph)
    return graph, complete


def _preparation_fingerprint():
    """Osm znaku z parametru, ktere ovlivnuji vysledek pripravy. Otisk je v nazvu
    souboru cache, takze po zmene preferenci nebo prahu parovani se stara cache
    proste nenajde (a nova vznikne vedle)."""
    import hashlib

    material = repr((
        PREPARED_CACHE_VERSION,
        cost_parameters(),
        STREET_MATCH_MAX_M, STREET_MATCH_MAX_DEG, TRAIL_MATCH_MAX_M,
    ))
    return hashlib.sha256(material.encode()).hexdigest()[:8]


def _prepared_cache_path(source_path):
    # Skladano z casti, ne pres with_suffix: nazvy grafu obsahuji tecky
    # (walk_50.076_14.419_9.5km.graphml) a bylo by na nahodu, co je "pripona".
    return source_path.parent / f"{source_path.stem}.prepared-{_preparation_fingerprint()}.pkl"


def _load_prepared(source_path):
    """Pripraveny graf z pickle, nebo None. Cte se jen soubor, ktery jsme sami
    vytvorili vedle vlastniho graphml - pickle spousti kod, cizi soubor by se
    tu objevit nemel."""
    cached = _prepared_cache_path(source_path)
    if not cached.exists():
        return None
    try:
        with open(cached, "rb") as handle:
            return pickle.load(handle)
    except Exception:
        # poskozena nebo neprecitatelna cache nesmi shodit planovani
        cached.unlink(missing_ok=True)
        return None


def _store_prepared(source_path, graph):
    cached = _prepared_cache_path(source_path)
    temporary = cached.parent / (cached.name + ".tmp")
    try:
        with open(temporary, "wb") as handle:
            pickle.dump(graph, handle, protocol=pickle.HIGHEST_PROTOCOL)
        temporary.replace(cached)  # atomicky, at nevznikne pulka souboru
    except Exception:
        temporary.unlink(missing_ok=True)
        return
    # stare otisky tehoz grafu uz nikdo nepouzije
    for stale in source_path.parent.glob(f"{source_path.stem}.prepared-*.pkl"):
        if stale != cached:
            stale.unlink(missing_ok=True)


_GRAPH_MEMORY = {}


def load_walk_graph(lat, lon, reach_km):
    """Pesi graf OSM pokryvajici kruh (start, reach), pripraveny k planovani.

    Prvni stazeni z Overpass trva minuty; stahuje se velkoryse (min. 10 km).
    Dalsi volani berou graf z pameti procesu, jinak z pickle cache (~4 s),
    a teprve nakonec z graphml (~28 s vcetne pripravy)."""
    import osmnx as ox

    path = covering_graph_path(lat, lon, reach_km)
    if path is None:
        download_reach = max(reach_km, MIN_DOWNLOAD_REACH_KM)
        ox.settings.cache_folder = str(GRAPH_DIR / "osmnx_cache")
        graph = ox.graph_from_point(
            (lat, lon), dist=download_reach * 1000, network_type="walk", simplify=True
        )
        path = graph_path(lat, lon, download_reach)
        ox.save_graphml(graph, path)
        _, complete = _prepare(graph, lat, lon, download_reach)
        if complete:
            _store_prepared(path, graph)
        _GRAPH_MEMORY[str(path)] = graph
        return graph

    if str(path) not in _GRAPH_MEMORY:
        graph = _load_prepared(path)
        if graph is None:
            graph, complete = _prepare(ox.load_graphml(path), lat, lon, reach_km)
            if complete:
                _store_prepared(path, graph)
        _GRAPH_MEMORY[str(path)] = graph
    return _GRAPH_MEMORY[str(path)]


_NODE_INDEX_CACHE = {}


def node_index(graph):
    """(uzly, pole zem. sirek, pole delek) pro rychle hledani nejblizsiho uzlu."""
    import numpy as np

    key = id(graph)
    if key not in _NODE_INDEX_CACHE:
        nodes = list(graph.nodes)
        lats = np.array([graph.nodes[n]["y"] for n in nodes])
        lons = np.array([graph.nodes[n]["x"] for n in nodes])
        _NODE_INDEX_CACHE[key] = (nodes, lats, lons)
    return _NODE_INDEX_CACHE[key]


def nearest_node(index, lat, lon):
    import numpy as np

    nodes, lats, lons = index
    coslat = math.cos(math.radians(lat))
    d2 = (lats - lat) ** 2 + ((lons - lon) * coslat) ** 2
    return nodes[int(np.argmin(d2))]


def path_coordinates(graph, node_path):
    """Souradnice trasy vcetne geometrii hran (skutecne tvary ulic, ne jen
    spojnice krizovatek) - presnejsi GPX, mapa i vypocet protnutych tiles."""
    from runcost import best_edge

    if len(node_path) < 2:
        return [(graph.nodes[n]["y"], graph.nodes[n]["x"]) for n in node_path]

    coordinates = []
    for u, v in zip(node_path, node_path[1:]):
        edge = best_edge(graph, u, v)
        geometry = edge.get("geometry")
        if geometry is not None:
            points = [(lat, lon) for lon, lat in geometry.coords]
            u_lat, u_lon = graph.nodes[u]["y"], graph.nodes[u]["x"]
            starts_at_u = (abs(points[0][0] - u_lat) + abs(points[0][1] - u_lon)
                           <= abs(points[-1][0] - u_lat) + abs(points[-1][1] - u_lon))
            if not starts_at_u:
                points.reverse()
        else:
            points = [
                (graph.nodes[u]["y"], graph.nodes[u]["x"]),
                (graph.nodes[v]["y"], graph.nodes[v]["x"]),
            ]
        coordinates.extend(points if not coordinates else points[1:])
    return coordinates
