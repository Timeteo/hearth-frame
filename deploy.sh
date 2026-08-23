#!/bin/sh
# Deploy hearth-frame to the frame LXC (115 "web" on dantooine, 10.10.10.25).
# Photos/manifest/weather live under /frame/ there; page is served at /.
set -e
cd "$(dirname "$0")"
scp dist/hearth-frame.html dist/curate.html tools/build-manifest.py tools/frame-block.py root@dantooine:/tmp/
ssh root@dantooine "pct push 115 /tmp/hearth-frame.html /var/www/hearth-frame/hearth-frame.html --user 33 --group 33 && pct push 115 /tmp/curate.html /var/www/hearth-frame/curate.html --user 33 --group 33 && pct push 115 /tmp/build-manifest.py /opt/frame/build-manifest.py && pct push 115 /tmp/frame-block.py /opt/frame/frame-block.py && pct exec 115 -- systemctl restart frame-block.service"
echo deployed
