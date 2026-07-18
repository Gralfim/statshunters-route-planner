import json
import os
import re
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("STATSHUNTERS_BASE_URL", "https://www.statshunters.com/share")
USER_AGENT = "statshunters-route-planner"
PAGE_TIMEOUT_SECONDS = 30
MAX_PAGES = 100


def resolve_share_link(config):
    env_link = os.environ.get("STATSHUNTERS_SHARE_LINK")
    if env_link:
        return env_link
    return (config.get("statshunters") or {}).get("share_link") or ""


def share_code(share_link):
    match = re.search(r"share/([A-Za-z0-9]+)", share_link)
    if match:
        return match.group(1)

    code = share_link.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9]+", code):
        raise ValueError(f"Invalid StatsHunters share link: {share_link!r}")
    return code


def fetch_page(code, page):
    url = f"{BASE_URL}/{code}/api/activities"
    if page > 1:
        url = f"{url}?page={page}"

    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=PAGE_TIMEOUT_SECONDS) as response:
        return json.load(response)


def fetch_all_pages(share_link):
    code = share_code(share_link)
    pages = []

    for page in range(1, MAX_PAGES + 1):
        data = fetch_page(code, page)
        activities = data.get("activities", [])

        if pages and activities and activities[0].get("id") == pages[-1]["activities"][0].get("id"):
            raise RuntimeError("StatsHunters API returned the same page twice; pagination failed")
        if activities:
            pages.append(data)

        limit = (data.get("meta") or {}).get("limit")
        if not activities or not limit or len(activities) < limit:
            return pages

    raise RuntimeError(f"StatsHunters API returned more than {MAX_PAGES} pages; aborting")


def sync_activities(data_dir, share_link):
    pages = fetch_all_pages(share_link)
    total = sum(len(page["activities"]) for page in pages)
    if total == 0:
        raise RuntimeError("StatsHunters API returned no activities; keeping existing data files")

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(pages, start=1):
        path = data_dir / f"activities{index}.json"
        path.write_text(json.dumps(page, separators=(",", ":")), encoding="utf-8")

    for path in data_dir.glob("activities*.json"):
        suffix = path.stem.removeprefix("activities")
        if suffix.isdigit() and int(suffix) > len(pages):
            path.unlink()

    return {"pages": len(pages), "activities": total}
