import traceback
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from basemap import basemap_config
from cluster import find_largest_cluster
from expedition import build_targets, plan_expedition
from landmarks import annotate_directions
from frontier import frontier_tiles
from geojson import feature_collection, tile_feature, tile_outline_feature_collection
from load import load_activities
from routing import load_walk_graph, plan_tile_loop, route_to_gpx
from scoring import build_route_context, find_tile_opportunities
from square import find_largest_square
from statshunters import resolve_share_link, sync_activities
from tiles import build_tile_database
from transit import TransitNetwork, load_transit_graph


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.yaml"
WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="StatsHunters Route Planner")


@app.exception_handler(Exception)
def unhandled_exception(request: Request, exc: Exception):
    """Necekana chyba: plny traceback do logu serveru (dohledatelnost priciny)
    a zaroven typ+text do UI. Pokryva i chyby pri serializaci odpovedi, ktere
    uvnitr endpointu zachytit nelze (napr. nan v JSON). Ocekavane stavy vraci
    endpointy pres HTTPException a sem se nedostanou."""
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Chyba serveru: {type(exc).__name__}: {exc}"},
    )


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
            "color": "#2a78d6",
        },
        "year": {
            "label": f"Rok {today.year}",
            "start_date": date(today.year, 1, 1),
            "end_date": today,
            "color": "#eda100",
        },
        "recent": {
            "label": "Posledni 3 mesice",
            "start_date": _subtract_months(today, 3),
            "end_date": today,
            "color": "#e34948",
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


# Dlazdice v barvach obdobi; prohlizec si /favicon.ico vyzada i pri deklarovanem
# <link rel="icon">, takze se obsluhuje primo (jinak 404 v logu).
FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'>"
    "<rect width='16' height='16' fill='#2a78d6'/>"
    "<rect x='8' width='8' height='8' fill='#eda100'/>"
    "<rect y='8' width='8' height='8' fill='#e34948'/>"
    "</svg>"
)


@app.get("/favicon.ico")
def favicon():
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


@app.get("/api/health")
def health():
    return {"status":"ok"}


@app.get("/api/basemap")
def basemap():
    """Konfigurace podkladove mapy. Vlastni endpoint (ne soucast /api/summary),
    protoze summary staví tile databaze a pri prvnim dotazu trva sekundy -
    podklad ma byt v mape hned."""
    return basemap_config(get_config())


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
        "expedition_budget_min": config.get("expedition_budget_min", 120),
        "run_pace_min_per_km": config.get("run_pace_min_per_km", 6.0),
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


@lru_cache
def get_opportunities():
    return find_tile_opportunities({
        "all": get_period_tile_database("all"),
        "year": get_period_tile_database("year"),
        "recent": get_period_tile_database("recent"),
    })


@lru_cache
def get_route_context():
    return build_route_context({
        "all": get_period_tile_database("all"),
        "year": get_period_tile_database("year"),
        "recent": get_period_tile_database("recent"),
    })


@lru_cache
def get_transit_network():
    return TransitNetwork(load_transit_graph())


@lru_cache
def get_expedition_targets():
    return build_targets(get_opportunities(), get_route_context())


@app.get("/api/opportunities")
def opportunities_geojson():
    return feature_collection(
        tile_feature(opportunity["tile"], {
            "kind": "opportunity",
            "x": opportunity["tile"][0],
            "y": opportunity["tile"][1],
            "rank": opportunity["rank"],
            "score": opportunity["score"],
            "priority": opportunity["priority"],
            "top_reason": opportunity["top_reason"],
            "last_visit": opportunity["last_visit"],
            "days_since_visit": opportunity["days_since_visit"],
            "visited_periods": opportunity["visited_periods"],
            "missing_periods": opportunity["missing_periods"],
            "reasons": opportunity["reasons"],
            "gains": opportunity["gains"],
        })
        for opportunity in get_opportunities()
    )


def _annotate_landmarks(route, lat, lon, reach_km):
    """Doplni itinerar behu o krizeni vody/zeleznice. Stazeni bariér muze v nove
    oblasti trvat i selhat; trasa je pouzitelna i bez nich, takze se chyba
    nepropaguje do odpovedi - ale do logu patri, jinak by tise mizely
    orientacni body a nebylo by poznat proc."""
    try:
        annotate_directions(route["directions"], route["coordinates"], lat, lon, reach_km)
    except Exception:
        traceback.print_exc()


class RouteRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None
    distance_km: float | None = None
    tolerance_km: float | None = None


@app.post("/api/route")
def plan_route(request: RouteRequest):
    config = get_config()
    lat = request.lat if request.lat is not None else config["home"]["lat"]
    lon = request.lon if request.lon is not None else config["home"]["lon"]
    distance_km = request.distance_km if request.distance_km is not None else config["target_distance_km"]
    tolerance_km = request.tolerance_km if request.tolerance_km is not None else config["distance_tolerance_km"]

    if not 2 <= distance_km <= 42:
        raise HTTPException(status_code=400, detail="Delka trasy musi byt 2 az 42 km")
    if not 0.2 <= tolerance_km <= distance_km / 2:
        raise HTTPException(status_code=400, detail="Tolerance musi byt 0.2 km az polovina delky")

    reach_km = (distance_km + tolerance_km) / 2 + 0.5
    graph = load_walk_graph(lat, lon, reach_km)

    opportunities = get_opportunities()
    try:
        route = plan_tile_loop(graph, lat, lon, distance_km, tolerance_km, opportunities, get_route_context())
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    candidate_tiles = {tuple(item["tile"]) for item in opportunities}
    route["crossed_recommended"] = sum(1 for tile in route["tiles_crossed"] if tile in candidate_tiles)
    _annotate_landmarks(route, lat, lon, reach_km)
    route["gpx"] = route_to_gpx(route["coordinates"])
    return route


class ExpeditionRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None
    distance_km: float | None = None
    tolerance_km: float | None = None
    budget_min: float | None = None
    pace_min_per_km: float | None = None
    weekend: bool | None = None


@app.post("/api/expedition")
def plan_expedition_endpoint(request: ExpeditionRequest):
    config = get_config()
    lat = request.lat if request.lat is not None else config["home"]["lat"]
    lon = request.lon if request.lon is not None else config["home"]["lon"]
    distance_km = request.distance_km if request.distance_km is not None else config["target_distance_km"]
    tolerance_km = request.tolerance_km if request.tolerance_km is not None else config["distance_tolerance_km"]
    budget_min = request.budget_min if request.budget_min is not None else config.get("expedition_budget_min", 120)
    pace = request.pace_min_per_km if request.pace_min_per_km is not None else config.get("run_pace_min_per_km", 6.0)

    if not 2 <= distance_km <= 42:
        raise HTTPException(status_code=400, detail="Delka trasy musi byt 2 az 42 km")
    if not 0.2 <= tolerance_km <= distance_km / 2:
        raise HTTPException(status_code=400, detail="Tolerance musi byt 0.2 km az polovina delky")
    if not 20 <= budget_min <= 360:
        raise HTTPException(status_code=400, detail="Rozpocet vypravy musi byt 20 az 360 minut")
    if not 3 <= pace <= 12:
        raise HTTPException(status_code=400, detail="Tempo musi byt 3 az 12 min/km")

    weekend = request.weekend if request.weekend is not None else date.today().weekday() >= 5
    day = "weekend" if weekend else "weekday"

    try:
        plan = plan_expedition(
            lat, lon, distance_km, tolerance_km, budget_min, pace,
            get_opportunities(), get_route_context(), get_transit_network(), get_expedition_targets(),
            day=day,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    run = plan["route"]
    center_lat = sum(c[0] for c in run["coordinates"]) / len(run["coordinates"])
    center_lon = sum(c[1] for c in run["coordinates"]) / len(run["coordinates"])
    _annotate_landmarks(run, center_lat, center_lon, run["length_km"] / 2 + 1)
    run["gpx"] = route_to_gpx(run["coordinates"])
    return plan


@app.post("/api/sync")
def sync_data():
    share_link = resolve_share_link(get_config())
    if not share_link:
        raise HTTPException(
            status_code=400,
            detail="StatsHunters share link neni nastaven (config.yaml: statshunters.share_link)",
        )

    try:
        result = sync_activities(DATA_DIR, share_link)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=f"Stazeni ze StatsHunters selhalo: {exc}")

    get_activities.cache_clear()
    get_tile_database.cache_clear()
    get_period_tile_database.cache_clear()
    get_opportunities.cache_clear()
    get_route_context.cache_clear()
    get_expedition_targets.cache_clear()
    return result


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
