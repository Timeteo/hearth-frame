#!/bin/bash
# Nightly bounded-cache refresh from the configured Immich album.
set -euo pipefail

exec >>/var/log/frame-sync.log 2>&1
exec 9>/run/hearth-frame-immich-sync.lock
flock -n 9 || { echo "sync already running"; exit 0; }

echo "=== immich-sync $(date -Is) ==="
set -a
# shellcheck disable=SC1091
source /etc/hearth-frame/immich.env
set +a
/opt/frame/immich-sync.py
echo "done $(date -Is)"
