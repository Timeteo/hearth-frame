#!/usr/bin/env python3
"""Tiny curation endpoint for hearth-frame. Listens on 127.0.0.1:8484
(nginx proxies it LAN-only at /frame/block); systemd unit frame-block.service.

POST /frame/block   JSON {"f": "IMG_1234.jpg"}
    -> appends the basename to /opt/frame/blocklist.txt and removes the
       entry from the live manifest (atomic rewrite), so it stops showing
       immediately and stays excluded across nightly rebuilds.
POST /frame/block   JSON {"f": "...", "undo": true, "entry": {...}}
    -> removes the basename from the blocklist and, if "entry" (the manifest
       object the client saved before blocking) is given, re-inserts it.
POST /frame/block   JSON {"f": "...", "rotate": 90|180|270}
    -> rotates the JPEG on disk clockwise (mogrify) and swaps the manifest
       w/h for 90/270 — for old-camera photos with no orientation metadata.
"""
import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

BLOCKLIST = "/opt/frame/blocklist.txt"
MANIFEST = "/var/www/hearth-frame/frame/manifest.json"
PHOTOS = "/var/www/hearth-frame/frame/photos"
SAFE = re.compile(r"^[\w][\w .()\[\]&+',@-]*\.jpe?g$", re.IGNORECASE)


def rewrite_manifest(mutate):
    with open(MANIFEST, encoding="utf-8") as fh:
        m = json.load(fh)
    m["photos"] = mutate(m.get("photos", []))
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(m, fh, separators=(",", ":"))
    os.replace(tmp, MANIFEST)
    return len(m["photos"])


def read_blocklist():
    try:
        with open(BLOCKLIST, encoding="utf-8") as fh:
            return [ln.rstrip("\n") for ln in fh]
    except OSError:
        return []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0))
            # manifest "f" is a frame-dir-relative path (photos/<name>.jpg);
            # the blocklist and build-manifest filter use the basename.
            name = os.path.basename(str(body.get("f", "")))
            if not SAFE.match(name):
                return self.reply(400, {"error": "bad filename"})
            same = lambda p: os.path.basename(p.get("f", "")) == name
            if body.get("rotate"):
                deg = int(body["rotate"])
                if deg not in (90, 180, 270):
                    return self.reply(400, {"error": "bad angle"})
                path = os.path.join(PHOTOS, name)
                if not os.path.isfile(path):
                    return self.reply(404, {"error": "no such photo"})
                subprocess.run(["mogrify", "-rotate", str(deg), path], check=True)

                def swap(ph):
                    for p in ph:
                        if same(p) and deg != 180:
                            p["w"], p["h"] = p.get("h"), p.get("w")
                    return ph
                return self.reply(200, {"ok": True, "photos": rewrite_manifest(swap)})
            if body.get("undo"):
                lines = [ln for ln in read_blocklist() if ln.strip() != name]
                with open(BLOCKLIST, "w", encoding="utf-8") as fh:
                    fh.write("".join(ln + "\n" for ln in lines))
                entry = body.get("entry")
                n = rewrite_manifest(
                    lambda ph: ph + [entry]
                    if isinstance(entry, dict) and same(entry)
                    and not any(same(p) for p in ph) else ph)
            else:
                if name not in {ln.strip() for ln in read_blocklist()}:
                    with open(BLOCKLIST, "a", encoding="utf-8") as fh:
                        fh.write(name + "\n")
                n = rewrite_manifest(lambda ph: [p for p in ph if not same(p)])
            self.reply(200, {"ok": True, "photos": n})
        except Exception as e:  # malformed body / manifest IO — report, don't die
            self.reply(500, {"error": str(e)})

    def reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # quiet; journald gets errors via replies
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8484), Handler).serve_forever()
