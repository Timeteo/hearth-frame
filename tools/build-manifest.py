#!/usr/bin/env python3
"""Build manifest.json for hearth-frame from a folder of JPEGs.

Usage: build-manifest.py PHOTO_DIR [-o manifest.json]

Reads image dimensions + EXIF capture date/GPS with Pillow. Location names:
uses Google Takeout JSON sidecars (<name>.json / <name>.supplemental-metadata.json)
when present; sidecars carry only geoData lat/lon (never a place name), so those
are reverse-geocoded offline against GeoNames cities1000 (files in /opt/frame).

Junk filters (fork A curation): skips videos, PNGs/screenshots, images
smaller than MIN_PIXELS, extreme aspect ratios, junk filename patterns
(WhatsApp/Facebook/messenger saves), and images with no camera make/model
EXIF (memes, re-downloads, and app saves lack it; conversion preserves it).
Overrides: blocklist.txt (never include; appended by the frame-block service)
and allowlist.txt (always include even when a junk filter would drop it),
both in /opt/frame, one photo basename per line, # comments allowed.
"""
import json
import re
import sys
from pathlib import Path

from PIL import Image, ExifTags

MIN_PIXELS = 500_000          # skip thumbnails / icons / receipts-ish tiny files
MAX_ASPECT = 3.0              # skip screenshots-of-scrolls / panoramic strips
EXTS = {".jpg", ".jpeg"}

# saves from messaging/social apps — never camera photos
JUNK_NAME = re.compile(
    r"(^IMG-\d{8}-WA\d+)|(^FB_IMG)|(^received_)|(^\d+_\d+_\d+.*_[no]$)|"
    r"(^Screenshot)|(^screenshot)",
)

EXIF_DT = next(k for k, v in ExifTags.TAGS.items() if v == "DateTimeOriginal")
EXIF_MAKE, EXIF_MODEL = 271, 272


def read_list(path: Path):
    try:
        return {ln.strip() for ln in path.open(encoding="utf-8")
                if ln.strip() and not ln.startswith("#")}
    except OSError:
        return set()

GEONAMES_DIR = Path("/opt/frame")


class Geocoder:
    """Offline nearest-city lookup over GeoNames cities1000, bucketed by
    1-degree grid cells so each query scans only nearby rows."""

    def __init__(self, base: Path):
        self.grid = {}
        self.admin1 = {}
        self.cache = {}
        try:
            for line in (base / "admin1CodesASCII.txt").open(encoding="utf-8"):
                code, name = line.split("\t")[:2]
                self.admin1[code] = name
            for line in (base / "cities1000.txt").open(encoding="utf-8"):
                f = line.split("\t")
                lat, lon = float(f[4]), float(f[5])
                self.grid.setdefault((int(lat), int(lon)), []).append(
                    (lat, lon, f[1], f[8], f[10]))  # name, country, admin1
        except OSError:
            self.grid = {}          # data files missing -> geocoder disabled

    def lookup(self, lat, lon):
        if not self.grid:
            return None
        key = (round(lat, 2), round(lon, 2))   # ~1km cache buckets
        if key in self.cache:
            return self.cache[key]
        import math
        best, best_d = None, None
        cy, cx = int(lat), int(lon)
        for ring in range(4):                  # widen search up to ~3 degrees
            for dy in range(-ring, ring + 1):
                for dx in range(-ring, ring + 1):
                    if max(abs(dy), abs(dx)) != ring:
                        continue
                    for clat, clon, name, cc, a1 in self.grid.get((cy + dy, cx + dx), ()):
                        d = (clat - lat) ** 2 + ((clon - lon) * math.cos(math.radians(lat))) ** 2
                        if best_d is None or d < best_d:
                            best, best_d = (name, cc, a1), d
            if best is not None:
                break
        loc = None
        if best:
            name, cc, a1 = best
            region = self.admin1.get(f"{cc}.{a1}", "")
            # US reads best as "City, ST" (admin1 code); elsewhere "City, Region"
            loc = f"{name}, {a1}" if cc == "US" else f"{name}, {region or cc}"
        self.cache[key] = loc
        return loc


GEOCODER = Geocoder(GEONAMES_DIR)


def sidecar_loc(p: Path):
    for cand in (p.with_suffix(p.suffix + ".json"),
                 p.with_suffix(p.suffix + ".supplemental-metadata.json")):
        if cand.exists():
            try:
                meta = json.loads(cand.read_text())
                # Takeout: geoData + sometimes enrichments carry names; the
                # reliable human-readable one is in "location" of shared albums
                # or absent — fall back to reverse-geocoding geoData.
                name = (meta.get("location") or {}).get("name") if isinstance(
                    meta.get("location"), dict) else meta.get("location")
                if name:
                    return str(name)
                g = meta.get("geoData") or {}
                lat, lon = g.get("latitude") or 0, g.get("longitude") or 0
                if lat or lon:                 # (0,0) means "no GPS" in Takeout
                    return GEOCODER.lookup(lat, lon)
            except (json.JSONDecodeError, OSError):
                pass
    return None


def capture_date(img: Image.Image, p: Path):
    try:
        raw = (img._getexif() or {}).get(EXIF_DT)
        if raw:
            m = re.match(r"(\d{4}):(\d{2}):(\d{2})", raw)
            if m:
                return "-".join(m.groups())
    except Exception:
        pass
    # Takeout sidecar photoTakenTime as fallback
    for cand in (p.with_suffix(p.suffix + ".json"),
                 p.with_suffix(p.suffix + ".supplemental-metadata.json")):
        if cand.exists():
            try:
                ts = int(json.loads(cand.read_text())["photoTakenTime"]["timestamp"])
                import datetime
                return datetime.date.fromtimestamp(ts).isoformat()
            except Exception:
                pass
    return None


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    root = Path(sys.argv[1])
    out = Path(sys.argv[sys.argv.index("-o") + 1]) if "-o" in sys.argv \
        else root / "manifest.json"

    blocked = read_list(GEONAMES_DIR / "blocklist.txt")
    allowed = read_list(GEONAMES_DIR / "allowlist.txt")

    photos = []
    skipped = {"blocked": 0, "name": 0, "small": 0, "aspect": 0, "noexif": 0}
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in EXTS:
            continue
        if p.name in blocked:
            skipped["blocked"] += 1
            continue
        keep = p.name in allowed
        if not keep and JUNK_NAME.search(p.stem):
            skipped["name"] += 1
            continue
        try:
            with Image.open(p) as img:
                w, h = img.size
                if not keep:
                    if w * h < MIN_PIXELS:
                        skipped["small"] += 1
                        continue
                    if max(w, h) / max(1, min(w, h)) > MAX_ASPECT:
                        skipped["aspect"] += 1
                        continue
                    exif = img._getexif() or {}
                    if not (exif.get(EXIF_MAKE) or exif.get(EXIF_MODEL)):
                        skipped["noexif"] += 1
                        continue
                photos.append({
                    "f": str(p.relative_to(root)),
                    "w": w, "h": h,
                    "d": capture_date(img, p),
                    "loc": sidecar_loc(p),
                })
        except OSError:
            continue

    out.write_text(json.dumps({"photos": photos}, separators=(",", ":")))
    drops = ", ".join(f"{k} {v}" for k, v in skipped.items() if v)
    print(f"{len(photos)} photos -> {out}" + (f" (skipped: {drops})" if drops else ""))


if __name__ == "__main__":
    main()
