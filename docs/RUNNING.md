# Running ChargeGrid AI locally, step by step

This guide goes from a clean machine (or a freshly reset Docker) to a working app with
data. Commands run from the repository root (`ChargeGridAI/`).

---

## 1. What you need

| Need | Why |
|---|---|
| **Docker Desktop** | Runs the database, routing, tiles, API, workers and web app |
| **Docker Desktop → Settings → Resources: Memory ≥ 4 GB** | A planning run peaks near 1 GB, and the routing engine takes about 0.5 GB |
| **Docker Desktop → Settings → Resources: Disk limit ~40 GB** | Docker's disk file only grows. A cap stops it filling your Mac (see section 9) |
| **~25 GB free disk** before you start | Images, the database after seeding, and build cache |
| `make` (built into macOS) | Shortcuts for the commands below |
| Node 22 (optional) | Only for running frontend tests on the host |

Ports the stack uses: 8000 (API), 5173 (web app), 5433 (Postgres on the host), 3000
(map tiles), 8002 (routing), 6379 (Redis), 9000/9001 (MinIO), 5050 (MLflow). Stop
anything else using them first.

---

## 2. One-time setup

1. **Start Docker Desktop** and wait until it says *Engine running*.
2. **Create your settings file** (skip this if `.env` already exists; it holds your
   keys and is never committed):
   ```bash
   cp .env.example .env
   ```
3. **Fill in the keys you have** in `.env`. All are optional; each one turns a feature on:

   | Key | Turns on |
   |---|---|
   | `OCM_API_KEY` | Open Charge Map chargers (used by the seed step `ingest-ocm`) |
   | `ANTHROPIC_API_KEY` | The planning assistant at `/assistant` |
   | `GOOGLE_MAPS_SERVER_KEY` + `COST_CAP_MONTHLY_INR_GOOGLE` | Live Google Places (cost-guarded: calls stop once the monthly cap is reached). Google Solar only with `GOOGLE_SOLAR_ENABLED=true`; solar otherwise comes from NASA POWER |
   | `VITE_GOOGLE_MAPS_BROWSER_KEY` | The **Google Maps** basemap switch (top of the map) and live Google places near a selected site |

   Leave `APP_ENV=dev`. In dev, sign-in is optional: you act as an anonymous planner in
   the default organisation.

---

## 3. Start the stack

```bash
make up
```

This builds the images and starts every service. The first build downloads and
builds several GB and takes about 10–15 minutes. Later starts take seconds; use
`docker compose up -d` to start without rebuilding.

Check it's up:

```bash
docker compose ps                 # every service "Up"; postgis/redis/martin "healthy"
curl http://localhost:8000/health # {"status":"ok",...}
```

The routing engine (Valhalla) builds its road graph from
`infra/docker/valhalla/custom_files/mumbai_pune.osm.pbf` on first start. The built tiles
are kept in that folder on your Mac, so this happens only once, even after a Docker
reset.

---

## 4. Load the data (first time, or after a Docker reset)

```bash
make seed-pune
```

That one command runs these steps in order. If one fails, fix the cause and run it
again: every step is safe to repeat.

| # | Step (run alone with `make <step>`) | What it does |
|---|---|---|
| 1 | `migrate` | Creates the database tables |
| 2 | `ingest-osm` | Imports roads, places, substations and boundaries for Pune and the Mumbai–Pune corridor from `data/osm/mumbai_pune_full.osm.pbf`, then restarts the tile server |
| 3 | `ingest-chargers` | Existing chargers from OpenStreetMap, then fuses the sources |
| 4 | `ingest-ocm` | Chargers from Open Charge Map (**needs `OCM_API_KEY`**) |
| 5 | `ingest-vahan-synthetic` | Loads **synthetic** vehicle registrations. These are clearly labelled stand-ins until real VAHAN data exists |
| 6 | `ingest-population` | WorldPop population on the H3 grid (downloads a raster) |
| 7 | `ingest-solar` | NASA POWER solar irradiance (downloads from NASA) |
| 8 | `demand` | EV forecast, charging demand and gap for the Pune region |
| 9 | `seed-corridor` | Population and demand for the Mumbai–Pune corridor |
| 10 | `ingest-policy` | Indexes the policy PDFs for the assistant (downloads a 65 MB model the first time) |

Expect roughly an hour or more in total; the OSM import and demand runs take longest.
Without an OCM key, run the steps one by one and skip step 4:

```bash
make migrate ingest-osm ingest-chargers ingest-vahan-synthetic ingest-population \
     ingest-solar demand seed-corridor ingest-policy
```

---

## 5. Open the app

| URL | What |
|---|---|
| http://localhost:5173 | Home |
| http://localhost:5173/workspace | Map: chargers, demand and gap hexagons |
| http://localhost:5173/areas/1986140 | Area planner (Pune district) |
| http://localhost:5173/plans | Site planning: create a scenario, run it, see the plan |
| http://localhost:5173/runs/&lt;run-id&gt;/grid | Grid impact of a plan |
| http://localhost:5173/assistant | Planning assistant (needs `ANTHROPIC_API_KEY`) |
| http://localhost:5173/twin | Region-wide scenarios: add N chargers, adoption +30%, grid constraints |
| http://localhost:5173/data | Data catalogue and region readiness scores |
| http://localhost:5173/login | Sign in (optional in dev) |
| http://localhost:5173/admin | Audit log, config editor, members (admins) |
| http://localhost:8000/docs | API reference (interactive) |
| http://localhost:5050 | MLflow (calibration runs) |

**First plan:**
1. Open `/plans`.
2. Create a scenario: region, year 2028, DC 60/120 kW, a budget (e.g. ₹10 crore) and
   20 sites.
3. Start the run. It takes a few minutes. Progress streams on the page, then the plan,
   ranking, strategies and site economics appear.

**Google Maps:**
- Use the **OpenStreetMap / Google Maps** switch at the top of the Workspace and run
  maps. Our chargers, hexagons and sites are drawn on either map.
- On Google Maps, select a site, then **Google places within 500 m** lists nearby
  places live from Google. They are shown only there and never stored.

**On a run page:**
- **Export plan** buttons download Excel, PDF or GeoJSON.
- The **Phasing** tab splits the plan into 2026, 2028 and 2030 with a budget each.
- **Grid impact** shows load curves and grid assets.
- A site's **Energy** tab links to **Google map mode** when Google Solar covers its
  roof.

---

## 6. Optional services

- **Agents without a key (smoke test):**
  ```bash
  docker compose exec api python -m app.agents.smoke
  ```
  Runs the Mumbai–Pune example through every agent step with a scripted model.
- **Single sign-on (Keycloak):**
  ```bash
  docker compose --profile enterprise up -d keycloak
  ```
  See `infra/keycloak/README.md` for the test users and the `.env` lines. Uses about
  0.5 GB of memory.
- **Temporal workflows:**
  ```bash
  docker compose --profile temporal up -d
  ```
  Then set `JOB_RUNNER=temporal` in `.env` and restart the API.
- **Onboard another region:** see docs/onboarding-regions.md
  (`make onboard-region REGION=<id> PBF=<extract> DRY=1` shows the plan first).
- **Real grid or operator data:**
  - DISCOM asset file: `make ingest-discom CSV=/data/<file>.csv DISCOM=MSEDCL`, using the
    template in `data/templates/`.
  - OCPI operator feed: `make ingest-ocpi OPERATOR=<id>`, after adding the operator to
    `config/ocpi_operators.yaml`.

---

## 6b. Signing in and roles (optional in dev)

In dev (`APP_ENV=dev`, `AUTH_REQUIRED` unset) you act as an anonymous **planner** in
the `default` organisation. To try accounts and roles:

1. Register a user:
   ```bash
   curl -X POST localhost:8000/api/v1/auth/register -H 'Content-Type: application/json' \
     -d '{"email":"me@example.org","password":"a-long-password"}'
   ```
2. Give it a role in an organisation. Either make it a superuser in the database
   (`update app_user set is_superuser = true where email = ...`), or ask an admin to add
   it under Admin → Members.
3. Sign in at `/login`.

For single sign-on, start Keycloak (section 6) and follow infra/keycloak/README.md. To
enforce sign-in everywhere, set `AUTH_REQUIRED=true` in `.env` and restart the API.

## 7. Everyday use

```bash
docker compose up -d      # start (no rebuild)
docker compose stop       # stop, keep everything
make down                 # stop and remove containers, keep the data
docker compose logs -f api worker   # watch the API and worker
```

`docker compose down -v` also **deletes the database and all loaded data**. After it
you'd need section 4 again.

Rebuild the images only after changing Python or Node dependencies
(`backend/pyproject.toml`, `frontend/package.json`) or a Dockerfile:
`docker compose build api frontend`. The API and web app reload code changes
automatically. After backend changes, restart the workers with
`docker compose restart worker beat`.

---

## 8. Tests

```bash
make test-backend     # backend tests in the api container
make test-frontend    # frontend tests (needs Node 22 on the host)
make lint             # ruff, mypy, eslint
docker compose exec api pytest tests/security -q   # security checks (docs/security/pentest-checklist.md)
```

---

## 9. Keeping Docker's disk use down

Docker Desktop stores everything in one virtual disk file. **That file grows but never
shrinks on its own**, even after you delete images. Rebuilding images many times, as
during development, leaves old layers and build cache behind.

- **Cap it:** Docker Desktop → Settings → Resources → Disk usage limit (about 40 GB).
- **See what's using it:** `docker system df`
- **Free build cache and old images** (keeps your data):
  ```bash
  docker builder prune -f
  docker image prune -f
  ```
- **Remove this project's images** (keeps data volumes): `make docker-clean`
- **Reset Docker completely** (deletes everything in Docker, all projects): Docker
  Desktop → Troubleshoot → Clean / Purge data. Then go back to section 3.

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| Map shows no roads or layers | `docker compose restart martin` (it must start after the OSM import) |
| Run fails with "No candidate could be scored … Is the routing engine running?" | `docker compose up -d valhalla`, wait for `curl localhost:8002/status`, then rerun |
| A run stays "running" forever | The worker probably ran out of memory. Raise Docker's memory; the page marks the run failed |
| `/assistant` says it needs `ANTHROPIC_API_KEY` | Add the key to `.env`, then `docker compose restart api` |
| `Cannot connect to the Docker daemon` | Start Docker Desktop and wait for *Engine running* |
| "no space left on device" | Section 9 |

---

## 11. Save a portable copy, and restore it

Once the stack is seeded, save it so a Docker reset (or a new machine) doesn't mean
rebuilding and re-seeding:

```bash
make export-bundle                       # -> exports/chargegrid-bundle-<date>/
make export-bundle OUT=/Volumes/USB/cg   # or anywhere else, e.g. an external drive
```

The bundle holds:
- `images.tar.gz`: all the Docker images, so there's no rebuild;
- `chargegrid.dump`: the database, meaning all ingested data, runs, plans and agent
  sessions;
- `minio-data.tgz` and `mlflow-data.tgz`: the raw-file lake and the MLflow runs;
- `MANIFEST.txt`: checksums and the database schema version;
- a copy of `import-bundle.sh`.

It does **not** hold your `.env` (secrets). Keep a copy of that somewhere private.

To restore, into this checkout or a fresh one (the repository folder, with `config/`,
`data/` and your `.env`):

```bash
make import-bundle BUNDLE=exports/chargegrid-bundle-<date>
```

This replaces the current database and volumes: it asks first, or pass `--yes` to the
script. It checks the checksums, loads the images, restores the database and volumes,
and starts everything. It takes a few minutes, compared with more than an hour for
`make up` plus `make seed-pune`.
