from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from cluster import find_largest_cluster
from frontier import frontier_tiles
from geojson import feature_collection, tile_feature, tile_outline_feature_collection
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


def _subtract_months(day, months):
    month_index = day.year * 12 + day.month - 1 - months
    year = month_index // 12
    month = month_index % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    month_lengths = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(day.day, month_lengths[month - 1]))


def period_definitions(today=None):
    today = today or date.today()
    return {
        "all": {
            "label": "Celkem",
            "start_date": None,
            "end_date": today,
            "color": "#4f46e5",
        },
        "year": {
            "label": f"Rok {today.year}",
            "start_date": date(today.year, 1, 1),
            "end_date": today,
            "color": "#059669",
        },
        "recent": {
            "label": "Posledni 3 mesice",
            "start_date": _subtract_months(today, 3),
            "end_date": today,
            "color": "#dc2626",
        },
    }


@lru_cache
def get_period_tile_database(period_key):
    periods = period_definitions()
    if period_key not in periods:
        raise HTTPException(status_code=404, detail=f"Unknown period: {period_key}")

    period = periods[period_key]
    return build_tile_database(
        get_activities(),
        start_date=period["start_date"],
        end_date=period["end_date"],
    )


def _visited_tiles(period_key="all"):
    return list(get_period_tile_database(period_key).keys())


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
    config = get_config()
    periods = []

    for key, period in period_definitions().items():
        tile_db = get_period_tile_database(key)
        cluster = find_largest_cluster(tile_db.keys())
        square = find_largest_square(tile_db.keys())
        periods.append({
            "key": key,
            "label": period["label"],
            "color": period["color"],
            "start_date": period["start_date"].isoformat() if period["start_date"] else None,
            "end_date": period["end_date"].isoformat(),
            "run_tiles": len(tile_db),
            "frontier_tiles": len(frontier_tiles(tile_db)),
            "largest_cluster": cluster["size"],
            "largest_square": square["size"],
        })

    return {
        "activities": len(activities),
        "home": config["home"],
        "target_distance_km": config["target_distance_km"],
        "distance_tolerance_km": config["distance_tolerance_km"],
        "periods": periods,
    }


@app.get("/api/periods/{period_key}/tiles")
def tiles_geojson(period_key: str):
    tile_db = get_period_tile_database(period_key)
    return feature_collection(
        tile_feature(tile, {**_tile_props(tile, rec), "period": period_key})
        for tile, rec in tile_db.items()
    )


@app.get("/api/periods/{period_key}/frontier")
def frontier_geojson(period_key: str):
    return feature_collection(
        tile_feature(tile, {"x": tile[0], "y": tile[1], "kind": "frontier", "period": period_key})
        for tile in frontier_tiles(get_period_tile_database(period_key))
    )


@app.get("/api/periods/{period_key}/cluster")
def cluster_geojson(period_key: str):
    cluster = find_largest_cluster(_visited_tiles(period_key))
    return tile_outline_feature_collection(
        cluster["tiles"],
        {"kind": "cluster", "period": period_key, "size": cluster["size"]},
    )


@app.get("/api/periods/{period_key}/square")
def square_geojson(period_key: str):
    square = find_largest_square(_visited_tiles(period_key))
    return tile_outline_feature_collection(
        square["tiles"],
        {"kind": "square", "period": period_key, "size": square["size"]},
    )


@app.get("/api/tiles")
def all_tiles_geojson():
    return tiles_geojson("all")


@app.get("/api/frontier")
def all_frontier_geojson():
    return frontier_geojson("all")


@app.get("/api/cluster")
def all_cluster_geojson():
    return cluster_geojson("all")


@app.get("/api/square")
def all_square_geojson():
    return square_geojson("all")


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
