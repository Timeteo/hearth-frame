#!/bin/sh
# Deploy hearth-frame with the real token injected (token lives in untracked .token)
set -e
cd "$(dirname "$0")"
sed "s|__HA_TOKEN__|$(cat .token)|" dist/hearth-frame.html | ssh root@ha.home "cat > /config/www/hearth-frame.html"
echo deployed
