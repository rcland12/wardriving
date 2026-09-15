# Wardrive Analyzer

The web app for the sessions the Pi uploads to the home server, deployed at
**https://wardrive.russellland.dev** from rustyserver's docker compose stack, behind Keycloak.

- **Map**: every Wi-Fi network and Bluetooth device at the spot its signal peaked, drawn with
  the Pi's colorblind-safe security colors _and_ shapes. Street, dark and satellite basemaps;
  routes from the GPS track; clustering; a density heatmap. Hover for a summary, click for
  details. The map re-frames itself on whatever a search or filter leaves, and the button above
  the zoom controls recenters on everything shown.
- **Filters**: search (text or regex) across SSID, MAC, manufacturer and encryption; security;
  band and channel; minimum signal; sessions and dates; seen in N+ sessions; hidden SSIDs; WPS;
  manufacturer; only what's in view. Filters live in the URL, so any view can be bookmarked.
- **Details**: encryption and login type, WPS device name, band/channel/width, signal range,
  location, every session that saw it, signal over time, where it was heard (on the map), and
  the raw Kismet record.
- **Table**: sortable list of the filtered devices, in sync with the map.
- **Stats**: security mix, manufacturers, devices per session (new vs. seen before), channel
  use, signal distribution, common SSIDs. Click a bar to open those devices on the map.
- **Sessions**: distance, duration and counts per drive; check, fix, and publish to WiGLE.
- **Demo**: the header switch shows the Pi's demo-mode uploads instead. They're kept apart
  from real captures and can never be uploaded.

## How data flows

```
Pi ─► api.russellland.dev /wardrive/upload ─► ../rustyserver/api/data/wardrive/<session>/*.gz
                              │                     (source of truth; mounted read-only here)
                              └─► POST /api/admin/ingest {session, demo}   (X-Admin-Token)
                                        │
                                        ▼
                         Postgres `wardrive` (shared rustyserver-db)
                         sessions · devices · sightings · tracks · reviews · wigle_uploads
                                        │
browser ─► nginx ─► oauth2-proxy (Keycloak) ─► this app ─► /api/devices, /api/tracks, ...
```

- **Ingest** (`src/lib/server/ingest.ts`) unpacks each `.kismet.gz` to `/tmp`, reads it with
  `node:sqlite`, and replaces that session's devices, sightings and tracks in one transaction.
  It runs when the api announces an upload, at startup, every `WARDRIVE_SCAN_MINUTES`, and from
  the rescan buttons. A session is re-read only if its Kismet file changed or `PARSER_VERSION`
  in `kismet.ts` was bumped (bump it whenever what's extracted changes).
- **Reviews** stay in `tools/wardrive_review.py`. The app runs it with `--no-record` and
  `--reviewed-csv <temp file>`: the repaired CSV from **Fix** is stored in `reviews`, loaded back
  for a check or upload, and every WiGLE upload is recorded in `wigle_uploads` along with who
  did it. Nothing is ever written next to the original files.
- **Raw Kismet records** aren't copied into Postgres; they're read from the session file when
  you open one.
- The browser still filters the full device list itself (instant at this size). Past roughly
  50k unique devices that should move into SQL; `src/lib/server/library.ts` is where.

## Publishing to WiGLE

Sessions → **WiGLE…** on a session:

1. A dry run runs the same checks as `wardrive_review.py wigle --dry-run`. If the WiGLE CSV has
   problems (every session from Kismet 2025.09 does: it writes every network as open), the
   dialog offers **Fix and re-check**, which stores a repaired copy in Postgres.
2. It shows exactly what would be sent: rows, size, reviewed or original, and the commercial-use
   (`WIGLE_DONATE`) setting.
3. Nothing is sent until you tick the confirmation and press **Upload to WiGLE**.

A session already in `wigle_uploads` is refused. **Processing status** shows WiGLE's queue for
your account; **Fix all unreviewed** repairs every pending session in one go.

## Deploying

From `../rustyserver`:

```bash
docker compose build wardrive && docker compose up -d wardrive
docker compose logs -f wardrive          # one JSON line per event: ingest, review, admin_ingest
```

The service definitions (`wardrive`, `wardrive-proxy`), the nginx vhost, and the "why" behind
them are documented in rustyserver's `CLAUDE.md` > Wardrive analyzer. The image is built from
this repo's root (`analyzer/Dockerfile`, with `Dockerfile.dockerignore` allowlisting the context).

## Configuration

`../rustyserver/wardrive/wardrive.env` (template: `wardrive.env.example` next to it):

| Variable                                            | Default                         |                                                              |
| --------------------------------------------------- | ------------------------------- | ------------------------------------------------------------ |
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD`               | `postgres`, 5432, `wardrive` ×2 | Role and database from `postgres/entrypoint/50-wardrive.sql` |
| `ADMIN_API_TOKEN`                                   | none                            | = `WARDRIVE_ADMIN_TOKEN` in `api/api.env`; min 32 chars      |
| `WIGLE_API_NAME`, `WIGLE_API_TOKEN`, `WIGLE_DONATE` | none                            | Passed only to the review tool                               |
| `ANALYZER_ALLOWED_HOSTS`                            | none                            | `wardrive.russellland.dev`                                   |
| `ANALYZER_TRUSTED_PROXY`                            | none (check off)                | `172.20.0.250`, nginx: the only peer the app talks to        |
| `WARDRIVE_SCAN_MINUTES`                             | `15`                            | Backstop scan interval                                       |
| `WARDRIVE_DATA`                                     | `/data/wardrive`                | Set in the image and compose                                 |

## Safety

- Only nginx can reach the UI and API (peer address pinned), and nginx only lets a request
  through after oauth2-proxy confirms a Keycloak session in the `wardrive-users` group.
- Any other `Host` is refused. `/api/admin/*` needs the admin token and is 404 at nginx.
- The data mount and the container filesystem are read-only; the app writes only to `/tmp`
  and its own database.
- Actions are JSON POSTs, which a cross-site form can't send. An upload must name its session
  in the request body, session names must match a session in the database exactly, and demo
  sessions are refused before the tool runs (and again by the tool).

Basemap tiles come from OpenStreetMap (street and dark) and Esri World Imagery (satellite);
each map shows its attribution.
