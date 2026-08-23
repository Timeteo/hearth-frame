#!/bin/bash
# hearth-frame photo pipeline: Google Takeout (in Drive, via rclone) -> JPEGs -> manifest.
# Cron-driven. Safe to re-run; incremental at the ZIP level: each archive is
# downloaded, extracted, converted, and deleted before the next one starts, so
# peak disk stays ~one-zip-sized (10 GB zip => ~25 GB working set) even though
# a full export (~415 GB) is far larger than the LXC disk.
#
# PREREQ (one-time, interactive): rclone config -> remote "gdrive" (Google Drive),
# and Google Takeout scheduled with "Save to Drive".
set -euo pipefail

REMOTE="gdrive:Takeout"                 # Drive folder Takeout drops archives into
RAW=/opt/frame/raw                      # per-zip working area + done markers
PHOTOS=/var/www/hearth-frame/frame/photos
MANIFEST=/var/www/hearth-frame/frame/manifest.json
LOG=/var/log/frame-sync.log
MAX_EDGE=2000                           # downscale long edge (Portal is 1280x800)
QUALITY=85
MIN_FREE_KB=$((30 * 1024 * 1024))       # skip run if < 30 GB free (one-zip working set + slack)

exec >>"$LOG" 2>&1
echo "=== frame-sync $(date -Is) ==="

command -v rclone >/dev/null || { echo "rclone missing"; exit 1; }
rclone listremotes | grep -q '^gdrive:' || { echo "rclone remote 'gdrive' not configured — run: rclone config"; exit 1; }

mkdir -p "$RAW" "$PHOTOS"

if ! rclone lsd "$REMOTE" >/dev/null 2>&1; then
    echo "no $REMOTE folder in Drive yet (first Takeout export not delivered) — nothing to do"
    exit 0
fi

convert_tree() {  # convert everything under $1 into $PHOTOS, then delete sources
    local root=$1 src base out
    find "$root" -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.heic' \) | while read -r src; do
        base=$(basename "$src"); base="${base%.*}"
        case "$base" in Screenshot*|screenshot*) continue;; esac
        out="$PHOTOS/${base}.jpg"
        if [ ! -e "$out" ]; then
            if [[ "${src,,}" == *.heic ]]; then
                heif-convert -q "$QUALITY" "$src" "$out.tmp.jpg" >/dev/null 2>&1 || { echo "heif fail: $src"; continue; }
                { mogrify -auto-orient -resize "${MAX_EDGE}x${MAX_EDGE}>" "$out.tmp.jpg" && mv "$out.tmp.jpg" "$out"; } || { echo "resize fail: $src"; rm -f "$out.tmp.jpg"; continue; }
            else
                convert "$src" -auto-orient -resize "${MAX_EDGE}x${MAX_EDGE}>" -quality "$QUALITY" "$out" || { echo "convert fail: $src"; continue; }
            fi
            # carry the Takeout sidecar for date/location metadata
            for sc in "$src.json" "$src.supplemental-metadata.json"; do
                [ -e "$sc" ] && cp -n "$sc" "$PHOTOS/$(basename "$out").json"
            done
        fi
        rm -f "$src"
    done
}

# Process one archive at a time: download -> extract (skip videos) -> convert -> delete,
# rebuilding the manifest after each so photos go live incrementally.
# A .done-<name> marker makes re-runs skip archives already fully processed.
while read -r name; do
    [ -n "$name" ] || continue
    marker="$RAW/.done-$name"
    [ -e "$marker" ] && continue

    free_kb=$(df --output=avail -k / | tail -1 | tr -d ' ')
    if [ "$free_kb" -lt "$MIN_FREE_KB" ]; then
        echo "only $((free_kb / 1024 / 1024)) GB free (< 30 GB) — stopping before $name; will resume next run"
        break
    fi

    echo "processing $name"
    work="$RAW/work"
    rm -rf "$work"; mkdir -p "$work"
    rclone copyto "$REMOTE/$name" "$work/$name" -v || { echo "download fail: $name — will retry next run"; rm -rf "$work"; continue; }
    unzip -qo "$work/$name" -d "$work/extracted" \
        -x '*.mp4' '*.MP4' '*.mov' '*.MOV' '*.m4v' '*.avi' '*.gif' '*.mkv' '*.webm' \
        || echo "unzip warnings for $name (continuing with what extracted)"
    rm -f "$work/$name"
    convert_tree "$work/extracted"
    rm -rf "$work"
    touch "$marker"
    # rebuild the manifest per archive so the frame picks up new photos as the
    # pass progresses instead of only at the end (scan frame/ so f paths come
    # out as photos/<name>.jpg, which the page resolves relative to its frame/ prefix)
    python3 /opt/frame/build-manifest.py "$(dirname "$PHOTOS")" -o "$MANIFEST.tmp" && mv "$MANIFEST.tmp" "$MANIFEST"
done < <(rclone lsf "$REMOTE" --include '*.zip' --include '*.tgz' | sort)

echo "done $(date -Is)"
