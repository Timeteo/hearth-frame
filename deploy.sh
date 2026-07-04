#!/bin/sh
# Deploy hearth-frame (no secrets in the page; weather comes from weather.json)
set -e
cd "$(dirname "$0")"
scp dist/hearth-frame.html root@ha.home:/config/www/
echo deployed
