def tile_feature(tile, props):
    return {
        "type":"Feature",
        "properties":props,
        "geometry":{
            "type":"Point",
            "coordinates":[tile[0], tile[1]]
        }
    }
