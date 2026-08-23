#!/usr/bin/env python3
"""Build a bounded hearth-frame cache from one Immich album.

Immich remains the catalog of record. This script fetches album metadata,
selects recent / seasonal / random pools, downloads only preview JPEGs, and
atomically publishes a flat manifest whose entries carry a ``pool`` label.
The previous manifest remains live unless the complete new cache succeeds.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path


DEFAULT_CACHE = Path("/var/www/hearth-frame/frame/immich")
DEFAULT_MANIFEST = Path("/var/www/hearth-frame/frame/manifest.json")


def api_json(url: str, key: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url.rstrip("/") + "/api" + path,
        data=data,
        headers={"x-api-key": key, "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def fetch_assets(url: str, key: str, album_id: str) -> list[dict]:
    assets: list[dict] = []
    page: int | str = 1
    while page:
        result = api_json(url, key, "/search/metadata", {
            "albumIds": [album_id],
            "type": "IMAGE",
            "withExif": True,
            "size": 1000,
            "page": int(page),
        })
        section = result.get("assets") or {}
        assets.extend(section.get("items") or [])
        page = section.get("nextPage")
        print(f"metadata: {len(assets)} assets", file=sys.stderr)
    return assets


def parse_date(asset: dict) -> dt.date | None:
    # localDateTime preserves the calendar date seen by the photographer.
    value = asset.get("localDateTime") or (asset.get("exifInfo") or {}).get("dateTimeOriginal")
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def anniversary_delta(day: dt.date, today: dt.date) -> int:
    values = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidate = day.replace(year=year)
        except ValueError:  # Feb 29 in a non-leap year
            candidate = dt.date(year, 2, 28)
        values.append(abs((candidate - today).days))
    return min(values)


def stable_rng(label: str) -> random.Random:
    seed = int.from_bytes(hashlib.sha256(label.encode()).digest()[:8], "big")
    return random.Random(seed)


def sample(items: list[dict], count: int, rng: random.Random) -> list[dict]:
    if len(items) <= count:
        result = list(items)
        rng.shuffle(result)
        return result
    return rng.sample(items, count)


def select_assets(assets: list[dict], album_id: str, today: dt.date,
                  total: int, recent_share: float, seasonal_share: float,
                  recent_days: int, window_days: int,
                  blocked: set[str] | None = None) -> list[tuple[dict, str]]:
    blocked = blocked or set()
    eligible = []
    for asset in assets:
        day = parse_date(asset)
        if (asset.get("type") != "IMAGE" or asset.get("isTrashed") or asset.get("isOffline") or
                not day or f"{asset.get('id')}.jpg" in blocked):
            continue
        eligible.append((asset, day))

    recent_target = round(total * recent_share)
    seasonal_target = round(total * seasonal_share)
    random_target = total - recent_target - seasonal_target
    week = today.isocalendar()
    rng = stable_rng(f"{album_id}:{week.year}-W{week.week:02d}")

    recent_candidates = [a for a, day in eligible if 0 <= (today - day).days <= recent_days]
    recent_candidates.sort(key=lambda a: parse_date(a) or dt.date.min, reverse=True)
    # Prefer newest additions, then shuffle their on-screen order.
    recent = recent_candidates[:recent_target]
    rng.shuffle(recent)
    used = {a["id"] for a in recent}

    by_exact_date: dict[dt.date, list[dict]] = defaultdict(list)
    for asset, day in eligible:
        if asset["id"] in used or day.year >= today.year or anniversary_delta(day, today) > window_days:
            continue
        by_exact_date[day].append(asset)
    seasonal_candidates = []
    for day in sorted(by_exact_date):
        seasonal_candidates.extend(sample(by_exact_date[day], 6, rng))
    seasonal = sample(seasonal_candidates, seasonal_target, rng)
    used.update(a["id"] for a in seasonal)

    random_candidates = [a for a, _ in eligible if a["id"] not in used]
    random_pool = sample(random_candidates, random_target, rng)
    used.update(a["id"] for a in random_pool)

    # Backfill undersized pools without duplicating assets.
    if len(used) < min(total, len(eligible)):
        remainder = [a for a, _ in eligible if a["id"] not in used]
        random_pool.extend(sample(remainder, min(total - len(used), len(remainder)), rng))

    return ([(a, "recent") for a in recent] +
            [(a, "seasonal") for a in seasonal] +
            [(a, "random") for a in random_pool])


def location(asset: dict) -> str | None:
    exif = asset.get("exifInfo") or {}
    city, state, country = exif.get("city"), exif.get("state"), exif.get("country")
    parts = [p for p in (city, state if country in (None, "United States", "USA", "US") else country) if p]
    return ", ".join(dict.fromkeys(parts)) or None


def dimensions(asset: dict) -> tuple[int, int]:
    exif = asset.get("exifInfo") or {}
    w = int(exif.get("exifImageWidth") or asset.get("originalWidth") or 0)
    h = int(exif.get("exifImageHeight") or asset.get("originalHeight") or 0)
    orientation = str(exif.get("orientation") or "")
    if orientation in {"5", "6", "7", "8", "Rotate 90 CW", "Rotate 270 CW"}:
        w, h = h, w
    return w, h


def download_one(url: str, key: str, asset: dict, destination: Path) -> None:
    target = destination / f"{asset['id']}.jpg"
    request = urllib.request.Request(
        url.rstrip("/") + f"/api/assets/{asset['id']}/thumbnail?size=preview",
        headers={"x-api-key": key},
    )
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response, target.open("wb") as output:
                shutil.copyfileobj(response, output)
            if target.stat().st_size < 1024:
                raise OSError("preview response was unexpectedly small")
            return
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            target.unlink(missing_ok=True)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{asset['id']}: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--blocklist", type=Path, default=Path("/opt/frame/blocklist.txt"))
    parser.add_argument("--total", type=int, default=2000)
    parser.add_argument("--recent-share", type=float, default=0.25)
    parser.add_argument("--seasonal-share", type=float, default=0.30)
    parser.add_argument("--recent-days", type=int, default=90)
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    url = os.environ.get("IMMICH_URL", "")
    key = os.environ.get("IMMICH_API_KEY", "")
    album_id = os.environ.get("IMMICH_ALBUM_ID", "")
    if not all((url, key, album_id)):
        parser.error("IMMICH_URL, IMMICH_API_KEY, and IMMICH_ALBUM_ID are required")
    if args.total < 1 or args.recent_share < 0 or args.seasonal_share < 0 or args.recent_share + args.seasonal_share > 1:
        parser.error("invalid pool size/share configuration")

    today = dt.date.today()
    assets = fetch_assets(url, key, album_id)
    try:
        blocked = {line.strip() for line in args.blocklist.read_text().splitlines()
                   if line.strip() and not line.startswith("#")}
    except OSError:
        blocked = set()
    selected = select_assets(assets, album_id, today, args.total,
                             args.recent_share, args.seasonal_share,
                             args.recent_days, args.window_days, blocked)
    counts = {name: sum(pool == name for _, pool in selected) for name in ("recent", "seasonal", "random")}
    print(f"selected {len(selected)} of {len(assets)}: {counts}", file=sys.stderr)
    if args.dry_run:
        return

    args.cache.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="immich-staging-", dir=args.cache.parent))
    # tempfile defaults to 0700; nginx needs directory traversal after publish.
    staging.chmod(0o755)
    try:
        failed: set[str] = set()
        missing = []
        for asset, _ in selected:
            existing = args.cache / f"{asset['id']}.jpg"
            target = staging / existing.name
            if existing.is_file() and existing.stat().st_size >= 1024:
                try:
                    os.link(existing, target)
                except OSError:
                    shutil.copy2(existing, target)
            else:
                missing.append(asset)
        print(f"cache: reused {len(selected) - len(missing)}, downloading {len(missing)}", file=sys.stderr)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(download_one, url, key, asset, staging): asset
                       for asset in missing}
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                asset = futures[future]
                try:
                    future.result()
                except Exception as error:
                    failed.add(asset["id"])
                    print(f"preview failed: {error}", file=sys.stderr)
                if index % 100 == 0:
                    print(f"downloaded {index}/{len(futures)}", file=sys.stderr)

        minimum = max(1, int(len(selected) * 0.90))
        if len(selected) - len(failed) < minimum:
            raise RuntimeError(f"only {len(selected) - len(failed)}/{len(selected)} previews succeeded")
        if failed:
            selected = [(asset, pool) for asset, pool in selected if asset["id"] not in failed]
            counts = {name: sum(pool == name for _, pool in selected)
                      for name in ("recent", "seasonal", "random")}

        photos = []
        for asset, pool in selected:
            w, h = dimensions(asset)
            photos.append({
                "f": f"immich/{asset['id']}.jpg", "w": w, "h": h,
                "d": parse_date(asset).isoformat(), "loc": location(asset), "pool": pool,
            })
        manifest = {
            "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source": "immich", "albumId": album_id,
            "config": {"recentShare": args.recent_share, "seasonalShare": args.seasonal_share,
                       "recentDays": args.recent_days, "windowDays": args.window_days},
            "counts": counts, "photos": photos,
        }
        staged_manifest = args.manifest.with_suffix(".json.immich-new")
        staged_manifest.write_text(json.dumps(manifest, separators=(",", ":")))

        old_cache = args.cache.with_name(args.cache.name + ".old")
        shutil.rmtree(old_cache, ignore_errors=True)
        if args.cache.exists():
            os.replace(args.cache, old_cache)
        os.replace(staging, args.cache)
        os.replace(staged_manifest, args.manifest)
        shutil.rmtree(old_cache, ignore_errors=True)
        print(f"published {args.manifest}", file=sys.stderr)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
