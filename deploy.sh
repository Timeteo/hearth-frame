#!/bin/sh
# Deploy hearth-frame to the frame LXC (115 "web" on dantooine, 10.10.10.25).
# Photos/manifest/weather live under /frame/ there; page is served at /.
set -e
cd "$(dirname "$0")"
scp dist/hearth-frame.html tools/build-manifest.py root@dantooine:/tmp/
ssh root@dantooine "pct push 115 /tmp/hearth-frame.html /var/www/hearth-frame/hearth-frame.html --user 33 --group 33 && pct push 115 /tmp/build-manifest.py /opt/frame/build-manifest.py"
echo deployed
