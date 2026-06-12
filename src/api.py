from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cluster import find_largest_cluster
from frontier import frontier_tiles
from geojson import feature_collection, tile_feature
from load import load_activities
from square import find_largest_square
from tiles import build_tile_database


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.yaml"
WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="StatsHunters Route Planner")


@lru_cache
def get_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


@lru_cache
def get_activities():
    return load_activities(DATA_DIR)


@lru_cache
def get_tile_database():
    return build_tile_database(get_activities())


def _visited_tiles():
    return list(get_tile_database().keys())


def _tile_props(tile, rec):
    return {
        "x": tile.x,
        "y": tile.y,
        "visit_count": rec["visit_count"],
        "first_visit": rec["first_visit"].date().isoformat(),
        "last_visit": rec["last_visit"].date().isoformat(),
    }


@app.get("/api/health")
def health():
    return {"status":"ok"}


@app.get("/api/summary")
def summary():
    activities = get_activities()
    tile_db = get_tile_database()
    cluster = find_largest_cluster(_visited_tiles())
    square = find_largest_square(_visited_tiles())
    config = get_config()

    return {
        "activities": len(activities),
        "run_tiles": len(tile_db),
        "frontier_tiles": len(frontier_tiles(tile_db)),
        "largest_cluster": cluster["size"],
        "largest_square": square["size"],
        "home": config["home"],
        "target_distance_km": config["target_distance_km"],
        "distance_tolerance_km": config["distance_tolerance_km"],
    }


@app.get("/api/tiles")
def tiles_geojson():
    tile_db = get_tile_database()
    return feature_collection(
        tile_feature(tile, _tile_props(tile, rec))
        for tile, rec in tile_db.items()
    )


@app.get("/api/frontier")
def frontier_geojson():
    return feature_collection(
        tile_feature(tile, {"x": tile[0], "y": tile[1], "kind": "frontier"})
        for tile in frontier_tiles(get_tile_database())
    )


@app.get("/api/cluster")
def cluster_geojson():
    cluster = find_largest_cluster(_visited_tiles())
    return feature_collection(
        tile_feature(tile, {"x": tile[0], "y": tile[1], "kind": "cluster"})
        for tile in cluster["tiles"]
    )


@app.get("/api/square")
def square_geojson():
    square = find_largest_square(_visited_tiles())
    return feature_collection(
        tile_feature(tile, {"x": tile[0], "y": tile[1], "kind": "square"})
        for tile in square["tiles"]
    )


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
