"""Cena cesty pro beh a mereni po grafu.

Tady se rozhoduje, kudy trasa povede: `run_cost` je delka hrany vynasobena tim,
jak dobre se po ni bezi. Hledani cesty minimalizuje run_cost, ale delkova
tolerance se vzdy kontroluje proti SKUTECNYM metrum (`path_length_m`) - jinak by
zvyhodnena cyklostezka delala trasu opticky kratsi, nez je.

Modul zna jen hrany, ne dlazdice ani itinerar. Kdyz se ladi, jak trasy vypadaji,
je to prvni misto, kam sahnout.
"""
from geo import tag

# Preference typu cest pro beh (uzivatel 2026-07-19): cyklostezka > turisticka
# cesta/pesina > park a pesi zona > chodnik > klidna silnice; rusne silnice
# penalizovane, schody take.
RUN_PREFERENCES = {
    "cycleway": 0.60,
    "path": 0.70,
    "track": 0.70,
    "bridleway": 0.75,
    "pedestrian": 0.80,
    "footway": 0.85,
    "living_street": 0.95,
    "residential": 1.0,
    "service": 1.05,
    "unclassified": 1.05,
    "road": 1.1,
    "steps": 1.4,
    "tertiary": 1.35,
    "tertiary_link": 1.35,
    "secondary": 1.7,
    "secondary_link": 1.7,
    "primary": 2.2,
    "primary_link": 2.2,
    "trunk": 3.0,
    "trunk_link": 3.0,
}
DEFAULT_RUN_FACTOR = 1.1

# Typ cesty sam o sobe nestaci: v pesim grafu je pres 80 % delky `footway`, takze
# chodnik podel ctyrproude silnice vypada stejne dobre jako pesina v parku - a
# protoze je levnejsi nez klidna ulice (0,85 vs. 1,0), trasy se na velke tahy
# primo lepily (mereno na okruhu z Karlova nam.: 63 % delky podel ulic tridy
# tertiary+). Chodnikum a cestam vedenym podel vyznamne ulice (atribut
# along_major z waygraph.enrich_streets) se proto preference odebira. Faktor
# cestu nikdy nezlevni - jen zastropuje.
ALONG_MAJOR_FACTOR = 1.25
# Znacena turisticka trasa nebo cyklotrasa (atribut trail) byva vedena mimo
# provoz a za behu se po ni lepe orientuje - proto bonus.
TRAIL_BONUS = 0.85

# Typy cest, kterym se dohleda ulice, podel ktere vedou (chodniky/stezky bez
# vlastniho jmena). Jen u nich dava ALONG_MAJOR_FACTOR smysl - u pojmenovane
# ulice je jeji trida uz v `highway`.
NAMEABLE_HIGHWAYS = {"footway", "path", "track", "pedestrian", "steps", "cycleway", "bridleway"}

# Penalizace opakovaneho pruchodu stejnou ulici. Pri hledani useku se uz pouzita
# hrana zdrazi (REUSE_PENALTY x run_cost), takze se dalsi usek vraci jinudy.
REUSE_PENALTY = 4.0


def way_type_factor(highway):
    """Preference podle typu cesty. U sloucene hrany s vice typy rozhoduje ten
    nejlepsi - trasa po ni stejne povede tudy, kudy se da."""
    if isinstance(highway, (list, tuple)):
        return min(
            (RUN_PREFERENCES.get(value, DEFAULT_RUN_FACTOR) for value in highway),
            default=DEFAULT_RUN_FACTOR,
        )
    return RUN_PREFERENCES.get(highway, DEFAULT_RUN_FACTOR)


def edge_factor(edge):
    """Preference typu cesty upravena o kontext, ve kterem cesta vede: podel
    jake ulice (along_major) a jestli po ni jde znacena trasa (trail). Oboji
    doplnuje az obohaceni grafu, proto se ceny pocitaji po nem."""
    factor = way_type_factor(edge.get("highway"))
    if edge.get("along_major") and tag(edge, "highway") in NAMEABLE_HIGHWAYS:
        factor = max(factor, ALONG_MAJOR_FACTOR)
    if edge.get("trail"):
        factor *= TRAIL_BONUS
    return factor


def prepare_run_costs(graph):
    """Doplni hranam run_cost = delka x preference cesty (vcetne kontextu)."""
    for _u, _v, data in graph.edges(data=True):
        data["run_cost"] = float(data["length"]) * edge_factor(data)
    return graph


def cost_parameters():
    """Otisk vseho, co ovlivnuje run_cost. Slouzi k zneplatneni cache
    pripraveneho grafu: po zmene preferenci se ulozeny graf s puvodnimi cenami
    nesmi tise pouzit dal."""
    return (
        sorted(RUN_PREFERENCES.items()),
        DEFAULT_RUN_FACTOR,
        ALONG_MAJOR_FACTOR,
        TRAIL_BONUS,
        sorted(NAMEABLE_HIGHWAYS),
    )


def best_edge(graph, u, v):
    """Nejlepsi z paralelnich hran mezi dvema uzly (OSM jich mezi temiz
    krizovatkami vede vic - chodnik po obou stranach ulice apod.)."""
    return min(graph[u][v].values(), key=lambda edge: edge.get("run_cost", edge["length"]))


def edge_id(u, v):
    """Neorientovana identita ulice (usek mezi dvema krizovatkami)."""
    return (u, v) if u <= v else (v, u)


def route_weight(used_edges=None, quiet_factor=1.0, trail_factor=1.0):
    """Vaha pro dijkstru: run_cost navic upraveny podle toho, co ma varianta
    hledat jinudy.

    used_edges - hrany uz pouzite na trase (aby se okruh vracel jinou ulici),
    quiet_factor - prirazka cestam podel vyznamnych ulic,
    trail_factor - sleva cestam po znacene trase (aby je varianta vyhledavala,
    ne jen nahodou potkala).

    Vse se nasobi az nad run_cost, takze se tim NEmeni cenovy model grafu - je
    to strategie jedne varianty v portfoliu, ne jine nastaveni preferenci.
    """
    def weight(u, v, edges):
        best = min(edges.values(), key=lambda edge: edge.get("run_cost", edge["length"]))
        cost = best.get("run_cost", best["length"])
        if quiet_factor != 1.0 and best.get("along_major"):
            cost *= quiet_factor
        if trail_factor != 1.0 and best.get("trail"):
            cost *= trail_factor
        if used_edges is not None and edge_id(u, v) in used_edges:
            cost *= REUSE_PENALTY
        return cost
    return weight


def path_length_m(graph, node_path):
    """SKUTECNA delka trasy v metrech (ne cena) - proti ni se meri tolerance."""
    return float(sum(
        best_edge(graph, u, v)["length"]
        for u, v in zip(node_path, node_path[1:])
    ))


def repeated_m(graph, node_path):
    """Metry hran pouzitych na trase vicekrat (druhy+ pruchod stejnou ulici)."""
    from collections import Counter

    counts = Counter(edge_id(u, v) for u, v in zip(node_path, node_path[1:]))
    return float(sum(
        best_edge(graph, u, v)["length"] * (count - 1)
        for (u, v), count in counts.items() if count > 1
    ))


def trail_m(graph, node_path):
    """Metry trasy vedouci po znacene trase (turisticka znacka, cyklotrasa).

    Druha strana kvality behu: "nevede podel magistraly" je jen nepritomnost
    spatneho. Znacene trasy vedou udolimi, parky a podel vody - to je to, co
    trasu opravdu odlisi. Cenovy model je uz zvyhodnuje (TRAIL_BONUS), ale to
    rozhoduje jen uvnitr useku."""
    return float(sum(
        best_edge(graph, u, v)["length"]
        for u, v in zip(node_path, node_path[1:])
        if best_edge(graph, u, v).get("trail")
    ))


def along_major_m(graph, node_path):
    """Metry trasy vedouci podel vyznamne ulice (tertiary+).

    Skutecna mira kvality behu - typ cesty ji nezmeri, protoze chodnik podel
    magistraly a pesina v parku jsou oba `footway`. Cenovy model uz takove hrany
    zdrazuje (ALONG_MAJOR_FACTOR), ale to rozhoduje jen VNITRI useku; kolik z
    cele trasy takhle vede, se musi zmerit zvlast a dat do cilove funkce."""
    return float(sum(
        best_edge(graph, u, v)["length"]
        for u, v in zip(node_path, node_path[1:])
        if best_edge(graph, u, v).get("along_major")
    ))
