import argparse

import uvicorn

from api import DATA_DIR, app, get_activities, get_config, get_period_tile_database, period_definitions
from cluster import find_largest_cluster
from frontier import frontier_tiles
from square import find_largest_square
from statshunters import resolve_share_link, sync_activities


def print_stats():
    activities = get_activities()

    print("Activities:", len(activities))
    for key, period in period_definitions().items():
        tile_db = get_period_tile_database(key)
        cluster = find_largest_cluster(tile_db.keys())
        square = find_largest_square(tile_db.keys())
        start = period["start_date"].isoformat() if period["start_date"] else "beginning"
        end = period["end_date"].isoformat()

        print(f"\n{period['label']} ({start} - {end})")
        print("Run tiles:", len(tile_db))
        print("Frontier tiles:", len(frontier_tiles(tile_db)))
        print("Largest cluster:", cluster["size"])
        print("Largest square:", square["size"])


def run_sync():
    share_link = resolve_share_link(get_config())
    if not share_link:
        raise SystemExit(
            "Set statshunters.share_link in config.yaml (or the STATSHUNTERS_SHARE_LINK env var)"
        )

    try:
        result = sync_activities(DATA_DIR, share_link)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Sync failed: {exc}")

    print(f"Downloaded {result['activities']} activities in {result['pages']} pages to {DATA_DIR}")


def parse_args():
    parser = argparse.ArgumentParser(description="StatsHunters Route Planner")
    parser.add_argument("--stats", action="store_true", help="print data summary and exit")
    parser.add_argument("--sync", action="store_true", help="download fresh data from StatsHunters and exit")
    parser.add_argument("--host", default="127.0.0.1", help="server host")
    parser.add_argument("--port", default=8000, type=int, help="server port")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.sync:
        run_sync()
        return

    if args.stats:
        print_stats()
        return

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
