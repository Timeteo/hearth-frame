#!/usr/bin/env python3
"""Build manifest.json for hearth-frame from a folder of JPEGs.

Usage: build-manifest.py PHOTO_DIR [-o manifest.json]

Reads image dimensions + EXIF capture date/GPS with Pillow. Location names:
uses Google Takeout JSON sidecars (<name>.json / <name>.supplemental-metadata.json)
when present; otherwise leaves loc empty (offline reverse-geocode TBD).

Junk filters (fork A curation): skips videos, PNGs/screenshots, and images
smaller than MIN_PIXELS.
"""
import json
import re
import sys
from pathlib import Path

from PIL import Image, ExifTags

MIN_PIXELS = 500_000          # skip thumbnails / icons / receipts-ish tiny files
EXTS = {".jpg", ".jpeg"}

EXIF_DT = next(k for k, v in ExifTags.TAGS.items() if v == "DateTimeOriginal")


def sidecar_loc(p: Path):
    for cand in (p.with_suffix(p.suffix + ".json"),
                 p.with_suffix(p.suffix + ".supplemental-metadata.json")):
        if cand.exists():
            try:
                meta = json.loads(cand.read_text())
                # Takeout: geoData + sometimes enrichments carry names; the
                # reliable human-readable one is in "location" of shared albums
                # or absent — fall back to nothing rather than raw lat/lon.
                name = (meta.get("location") or {}).get("name") if isinstance(
                    meta.get("location"), dict) else meta.get("location")
                if name:
                    return str(name)
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

    photos = []
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in EXTS:
            continue
        try:
            with Image.open(p) as img:
                w, h = img.size
                if w * h < MIN_PIXELS:
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
    print(f"{len(photos)} photos -> {out}")


if __name__ == "__main__":
    main()
