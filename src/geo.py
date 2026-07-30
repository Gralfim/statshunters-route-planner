"""Geometrie na kouli, azimuty a cteni OSM tagu.

Nejnizsi vrstva planovani tras: nezavisi na zadnem jinem modulu projektu krome
`geojson` (prepocet dlazdic). Diky tomu smi cenovy model, priprava grafu i
itinerar sdilet jednu implementaci, aniz by hrozil kruhovy import.
"""
import math

from geojson import tile_lon_lat

EARTH_RADIUS_M = 6371000.0
# Delka jednoho stupne zemepisne sirky v metrech. U delky se jeste nasobi
# cos(sirky) - v nasich sirkach je to ~0,64, takze na to nelze zapomenout.
METERS_PER_DEGREE = 111320.0

_COMPASS = ["sever", "severovychod", "vychod", "jihovychod",
            "jih", "jihozapad", "zapad", "severozapad"]


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def tile_center(tile):
    x, y = tile
    lon, lat = tile_lon_lat(x + 0.5, y + 0.5)
    return lat, lon


def bearing(graph, u, v):
    """Azimut z uzlu u do uzlu v ve stupnich (0 = sever, po smeru hodinovych)."""
    lat1, lon1 = graph.nodes[u]["y"], graph.nodes[u]["x"]
    lat2, lon2 = graph.nodes[v]["y"], graph.nodes[v]["x"]
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass(bearing_deg):
    return _COMPASS[int((bearing_deg % 360) / 45 + 0.5) % 8]


def tag(edge, key):
    """Hodnota OSM tagu hrany. Osmnx uklada u slouceneho useku vic hodnot jako
    list (jedna cesta muze byt zcasti footway a zcasti steps) - bere se prvni."""
    value = edge.get(key)
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value
