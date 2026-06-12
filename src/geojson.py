import math


TILE_ZOOM = 14


def tile_xy(tile):
    if hasattr(tile, "x") and hasattr(tile, "y"):
        return (tile.x, tile.y)
    return (tile[0], tile[1])


def tile_lon_lat(x, y, zoom=TILE_ZOOM):
    n = 2.0 ** zoom
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_rad)
    return lon, lat


def tile_polygon(tile):
    x, y = tile_xy(tile)
    west, north = tile_lon_lat(x, y)
    east, south = tile_lon_lat(x + 1, y + 1)
    return [
        [west, north],
        [east, north],
        [east, south],
        [west, south],
        [west, north],
    ]


def tile_feature(tile, props=None):
    x, y = tile_xy(tile)
    return {
        "type":"Feature",
        "properties": props or {},
        "geometry":{
            "type":"Polygon",
            "coordinates":[tile_polygon((x, y))]
        }
    }


def feature_collection(features):
    return {"type": "FeatureCollection", "features": list(features)}
