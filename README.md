# ChargeGrid AI

Geospatial decision-intelligence platform for EV charging infrastructure
planning in India. Full product brief: `ChargeGrid_AI_Master_Dev_Prompt.md`.

**Status:** Phases 0–12 complete for the Pune Metropolitan Region and the Mumbai–Pune corridor.

| Phase | What works |
|---|---|
| 12. Scale-out | Region registry with Maharashtra and six metros registered as *planned*; per-region **data readiness scores** (Data page, `GET /readiness`) with the fix for each gap; onboarding script and playbook (`make onboard-region`, docs/onboarding-regions.md); **region-wide scenarios** (`/twin`): add N fast chargers, adoption +30%, where the grid constrains, also available to the assistant. |
| 11. Enterprise | Organisations and roles (viewer / planner / admin / org_admin) with org-scoped data; sign-in by email or single sign-on (Keycloak OIDC + PKCE); audit log; versioned admin config with rollback; licence-filtered exports (Excel, PDF, GeoJSON) with an attribution sheet; **phased plans** 2026/2028/2030 (Phasing tab); optional Temporal workflows; Google map mode for Google Solar results; production Dockerfiles, Helm chart, Terraform for AWS Mumbai, staging → prod pipeline, security scans and a pen-test checklist backed by tests. |
| 10. Grid & real data | Grid Impact page per plan (`/runs/:id/grid`): 24-hour load stacked by vehicle segment or charger class, charger profile, connections. Without DISCOM data it is a labelled proximity-only estimate (load added at each nearest OSM substation, confidence LOW); with a DISCOM asset file (`make ingest-discom`) it switches to transformer/substation utilisation, headroom and upgrade costs with measured confidence. OCPI 2.2.1 operator feeds (locations, CDRs/sessions) feed fusion and daily utilisation; `make calibrate` trains a LightGBM residual model against observed use and logs it beside the parametric model in MLflow. No DISCOM or operator data is connected yet. |
| 9. Agents | `/assistant`: ask a question or request a plan in plain language. A Claude planner turns it into a scenario and step plan; specialist agents (data, geo, demand, infrastructure, grid & energy, finance) call typed tools over the platform's own engines; planning requests pause for your confirmation (with the funnel and cost) before optimising; the report cites stored results and policy passages, and every number in it is checked against tool outputs (regenerated once, then unmatched sentences are removed and listed). Model calls are traced with prompt version, tokens and cost. The Mumbai–Pune corridor is a second region, and scenarios can be restricted to a route corridor. Needs `ANTHROPIC_API_KEY`. |
| 8. Solar & energy | Each planned site's Energy tab: solar + battery sizing by an hourly dispatch LP (12 representative days, time-of-day tariffs, demand charges) against grid-only, grid + solar and, for HT sites, an LT connection held by storage. It shows the recommendation's effect on the grid connection and OPEX. Solar from NASA POWER, or from Google Solar when a key is configured. |
| 7. Sizing & finance | Each planned site's Sizing tab: charger count from Erlang-C (P(wait > 10 min) ≤ 10% at peak) checked by a 1,000-day SimPy simulation, connector mix, LT/HT connection. Finance tab: CAPEX lines, 10-year cash flows, NPV/IRR/payback at P10/P50/P90 (5,000-draw Monte Carlo), tornado, pessimistic/base/optimistic cases, and an optional subsidy that is never assumed. |
| 6. Optimisation | A budget and site limit on a scenario produce a plan: which candidates to build and with how many chargers (budget-constrained capacitated MCLP on OR-Tools/HiGHS). "Why not?" re-solves explain strong sites left out. What-ifs (budget, site limit, adoption +30%) re-optimise in about 7 s and show what changed. The Strategies view compares maximum coverage, equity, cost-optimised and commercial plans on a Pareto chart. The map colours each area by the station serving it, or by the nearest one by drive time. |
| 5. Scoring | 10-minute drive-time catchments; eight sub-scores ranked under a weight profile, with a rank-robustness badge; site comparison against regional benchmarks (= 100). |
| 4. Candidates | Candidates from host POIs, highway corridors, demand-gap cells and user sites; 150 m dedupe; feasibility rules F01–F07, each rejection with its measurement. |
| 3. Demand | Bass EV forecasts (slow/base/fast, P10–P90), allocation to H3 cells, public charging demand and the gap; Area Planner. |
| 2. Data | Existing chargers fused from OSM and Open Charge Map with provenance; population on H3; data catalogue at `/data`. |

**Demand, costs and several thresholds are illustrative** until real VAHAN
data and sourced prices replace the synthetic registrations and placeholders,
and the UI says so wherever they are used. Every candidate is labelled as
needing field verification. See `docs/verification.md` for data-access
findings, `docs/adr/` for architecture decisions, and `docs/methodology/` for
how each model works.

## Local development

Step-by-step setup, seeding and troubleshooting: **[docs/RUNNING.md](docs/RUNNING.md)**.


```bash
cp .env.example .env    # then fill in keys as you get them
make up                 # build + start everything (renews node_modules volumes)
make seed-pune          # migrate + OSM + chargers + synthetic VAHAN + population + demand
make demand             # re-run forecasts/gap; MULTIPLIER=1.3 adds a what-if
make ingest-policy      # policy PDFs -> local embeddings for the assistant's policy search
make agent-evals        # golden agent evals on Claude (needs ANTHROPIC_API_KEY; CASES=a,b)
                        # planning runs: start from http://localhost:5173/plans
make test               # backend + frontend tests
make down
make docker-clean       # stop and remove this project's images and build cache
```

Open http://localhost:5173/workspace (map, charger stats and provenance,
demand/gap hexagons), http://localhost:5173/areas/1986140 (Area Planner),
http://localhost:5173/plans (site planning: candidates, feasibility, scores
and, with a budget, an optimised plan), http://localhost:5173/assistant (agents;
set `ANTHROPIC_API_KEY` in `.env` and `docker compose restart api` first), and
http://localhost:5173/data (data catalogue). Without a key,
`docker compose exec api python -m app.agents.smoke` runs the Mumbai–Pune example
through the whole agent graph with a scripted model.

Services: `api` (FastAPI, :8000), `frontend` (Vite, :5173), `postgis`
(:5433 on the host, since 5432 is often taken by a local Postgres; containers
use `postgis:5432`), `redis` (:6379), `minio` (:9000/:9001, Chainguard's build,
because MinIO no longer publishes community images), `valhalla` (:8002),
`martin` (:3000, serving only the layers in `infra/docker/martin/config.yaml`),
`mlflow` (:5050, since macOS AirPlay Receiver uses 5000), plus `worker` and
`beat` (Celery).

## Data sources and how to turn more on

| Source | Status | To enable |
|---|---|---|
| OpenStreetMap (roads, POIs, boundaries, chargers) | Loaded | `make ingest-osm ingest-chargers` |
| WorldPop 2025 population → H3 | Loaded (1 km grid, LOW confidence) | `make ingest-population GRID=100m` for the better 100 m grid (~0.8 GB, slow server) |
| VAHAN registrations | **Synthetic stand-in**, clearly labelled | Export from the Vahan dashboard to the CSV format in `backend/app/providers/government/vahan.py`, then `docker compose exec api python -m app.ingestion.cli vahan <csv>` |
| Open Charge Map | Loaded (23 Pune records; key in `.env`) | `make ingest-ocm` refreshes; the beat schedule runs it daily |
| BEE EV Yatra / BHEL | CSV importer ready | `make ingest-gov-chargers CSV=/data/raw/bee/<export>.csv` |
| Google Geocoding | Priced, but Google refuses calls until **billing is enabled** on the key's project | Enable billing in Google Cloud |
| Demand parameters (km/yr, kWh/km, public share, scrappage, supply) | **41 illustrative placeholders** | Fill the `value:` slots in `config/demand/*.yaml` with sourced numbers; the UI banner lists what's still a placeholder |
| RTO jurisdictions | Approximate (both PMR RTOs over Pune district) | Source jurisdiction boundaries, then set them in `config/demand/allocation.yaml` |
| Feasibility thresholds (F03 spacing and served ratio, F04 substation distance, F05 arterial distance) | **Illustrative placeholders** | Fill the `value:` slots in `config/policy/rules.yaml` from MoP guidelines / Maharashtra EV policy |
| Traffic counts / AADT | **None**; the traffic sub-score is a road-length proxy | Add a TomTom/HERE or AADT source |
| Income, housing type | **None**; those catchment indices show as unavailable, and equity covers access only | Add census/SECC or housing data |
| Host suitability for the land score | **Illustrative placeholders** | Fill `config/scoring.yaml` `land.host_suitability` |
| Solar resource | NASA POWER hourly, 2024, loaded (`make ingest-solar`) | Automatic |
| Google Maps basemap + live Google Places | **Enabled** (demo key): Google Maps basemap switch on Workspace and run pages with our layers on top; live places near a selected site (cost cap ₹1,000/month; never stored) | — |
| Google Solar | **Off**: solar comes from NASA POWER. The key's project doesn't have the Solar API enabled | Enable the Solar API on the Google Cloud project, set `GOOGLE_SOLAR_ENABLED=true` |
| PV / battery prices, ToD slabs, energy parameters | **Illustrative placeholders** | Fill `config/energy.yaml`, `config/costs/unit_costs.yaml` (pv/bess), ToD slabs in `config/tariffs/maharashtra.yaml` |
| Tariffs, selling price, OPEX, sizing parameters | **Illustrative placeholders**, used by sizing and finance (tariff stand-ins follow the MERC Case 217/2024 regime reported by secondary sources: ~₹5–5.5/kWh, demand charges waived) | Fill `config/tariffs/maharashtra.yaml` (MERC order), `config/finance.yaml`, `config/sizing/sizing.yaml` |
| Unit costs (chargers, civil, connection) | **Illustrative placeholders**, used by optimisation | Fill `config/costs/unit_costs.yaml` `value:` slots with quotes |
| Grid assets (substations) | OSM only, LOW confidence, so F04 never rejects | Ingest a MEDIUM+ source such as MSEDCL substation data |
| Claude (agents) | Adapter ready; `/assistant` returns 503 without a key | Set `ANTHROPIC_API_KEY` in `.env`, restart `api`; models in `config/agents.yaml` |
| Policy corpus (MoP 2024 guidelines, PM E-DRIVE PCS guidelines) | Loaded, 142 chunks, local embeddings | `make ingest-policy`; add PDFs in `config/policy/documents.yaml` |
| Place names (OSM place nodes) | Loaded with every OSM import (1,932 places) | Automatic |
| DISCOM grid assets | **None** (needs a data agreement); Grid Impact is proximity-only | Fill `data/templates/discom_assets_template.csv`, then `make ingest-discom CSV=/data/... DISCOM=MSEDCL` |
| Operator feeds (OCPI 2.2.1) | **None** configured; calibration logs "skipped" | Add the operator to `config/ocpi_operators.yaml` and `OCPI_TOKEN_<ID>` to `.env`, then `make ingest-ocpi OPERATOR=<id>`; beat runs it daily and `make calibrate` weekly |
| Mappls | Deliberately stubbed | Needs a terms review (its terms forbid display beside non-Mappls maps) |

## Repository layout

See Section 6 of the project brief. `backend/app/` holds the FastAPI app,
provider adapters, ingestion pipelines, and (later) engines and agents;
`frontend/` is the React/Vite/TypeScript UI; `config/` holds versioned
weights, tariffs, source registry, and prices.
