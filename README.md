# hearth-frame

Self-contained photo-frame web page for wall tablets running [Fully Kiosk],
styled to match the hearth-* card family (dark, typography-first, amber accent).
Built for a repurposed Facebook Portal Mini running a Home Assistant dashboard.

- Landscape photos fullscreen; portrait photos paired split-screen (Google
  Home Hub behavior); lone portraits centered on dark gutters.
- Overlay: clock, current conditions + daily H/L from a Home Assistant
  `weather` entity, and per-photo capture date + location.
- Zero dependencies, one HTML file, works on old WebViews (no backdrop-filter,
  no video). Survives WAN cutoff — everything is served locally.

## Install

1. Copy `dist/hearth-frame.html` to `/config/www/` on Home Assistant.
2. Put JPEGs in `/config/www/frame/` and generate the manifest:
   `python3 tools/build-manifest.py /config/www/frame/`
3. An HA automation publishes `frame/weather.json` (see meta-portal handoff); the page reads it unauthenticated — no tokens anywhere.
4. Fully Kiosk → Screensaver Wallpaper URL → `https://<ha>/local/hearth-frame.html`.

Manifest format: `{"photos":[{"f":"img.jpg","w":4032,"h":3024,"d":"2025-06-14","loc":"Carlsbad, California"}]}`
`f` may also be an absolute URL.

## Immich source

`tools/immich-sync.py` keeps a bounded 2,000-preview cache from one Immich
album rather than mirroring originals. The default mix is 25% photos from the
last 90 days, 30% photos captured within ±14 calendar days in past years, and
45% stable weekly-random memories. The browser rechecks date eligibility, so
seasonal photos automatically leave rotation as the calendar window moves.

Credentials live only on the LXC in `/etc/hearth-frame/immich.env` (mode 600):

```sh
IMMICH_URL=http://immich-host:2283
IMMICH_API_KEY=...
IMMICH_ALBUM_ID=...
```

Run with `/opt/frame/frame-sync-immich.sh`. The deployed cron definition in
`ops/frame-sync.cron` refreshes it nightly at 04:15.

MIT.
