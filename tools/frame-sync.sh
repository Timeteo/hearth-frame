#!/bin/bash
# hearth-frame photo pipeline: Google Takeout (in Drive, via rclone) -> JPEGs -> manifest.
# Cron-driven. Safe to re-run; incremental (skips already-converted files).
#
# PREREQ (one-time, interactive): rclone config -> remote "gdrive" (Google Drive),
# and Google Takeout scheduled every 2 months with "Save to Drive".
set -euo pipefail

REMOTE="gdrive:Takeout"                 # Drive folder Takeout drops archives into
RAW=/opt/frame/raw                      # downloaded+extracted takeout content
PHOTOS=/var/www/hearth-frame/frame/photos
MANIFEST=/var/www/hearth-frame/frame/manifest.json
LOG=/var/log/frame-sync.log
MAX_EDGE=2000                           # downscale long edge (Portal is 1280x800)
QUALITY=85

exec >>"$LOG" 2>&1
echo "=== frame-sync $(date -Is) ==="

command -v rclone >/dev/null || { echo "rclone missing"; exit 1; }
rclone listremotes | grep -q '^gdrive:' || { echo "rclone remote 'gdrive' not configured — run: rclone config"; exit 1; }

mkdir -p "$RAW" "$PHOTOS"

# 1. Pull new Takeout archives from Drive (folder appears with the first export)
if ! rclone lsd "$REMOTE" >/dev/null 2>&1; then
    echo "no $REMOTE folder in Drive yet (first Takeout export not delivered) — nothing to do"
    exit 0
fi
rclone copy "$REMOTE" "$RAW/zips" --include '*.zip' --include '*.tgz' -v

# 2. Extract any archives not yet extracted
for z in "$RAW"/zips/*.zip; do
    [ -e "$z" ] || continue
    marker="$RAW/.done-$(basename "$z")"
    [ -e "$marker" ] && continue
    echo "extracting $(basename "$z")"
    unzip -qo "$z" -d "$RAW/extracted" && touch "$marker"
done

# 3. Convert: HEIC->JPEG + downscale; JPEG->downscale. Junk filtering
#    (screenshots/PNGs/videos/tiny) happens here + in build-manifest.py.
find "$RAW/extracted" -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.heic' \) | while read -r src; do
    base=$(basename "$src"); base="${base%.*}"
    # skip obvious junk
    case "$base" in Screenshot*|screenshot*) continue;; esac
    out="$PHOTOS/${base}.jpg"
    [ -e "$out" ] && continue
    if [[ "${src,,}" == *.heic ]]; then
        heif-convert -q "$QUALITY" "$src" "$out.tmp.jpg" >/dev/null 2>&1 || { echo "heif fail: $src"; continue; }
        mogrify -resize "${MAX_EDGE}x${MAX_EDGE}>" "$out.tmp.jpg" && mv "$out.tmp.jpg" "$out"
    else
        convert "$src" -auto-orient -resize "${MAX_EDGE}x${MAX_EDGE}>" -quality "$QUALITY" "$out" || { echo "convert fail: $src"; continue; }
    fi
    # carry the Takeout sidecar for date/location metadata
    for sc in "$src.json" "$src.supplemental-metadata.json"; do
        [ -e "$sc" ] && cp -n "$sc" "$PHOTOS/$(basename "$out").json"
    done
done

# 4. Rebuild manifest (scan frame/ so f paths come out as photos/<name>.jpg,
#    which the page resolves relative to its own frame/ prefix)
python3 /opt/frame/build-manifest.py "$(dirname "$PHOTOS")" -o "$MANIFEST.tmp" && mv "$MANIFEST.tmp" "$MANIFEST"
echo "done $(date -Is)"
