# Region onboarding playbook (Phase 12)

This takes a region from `status: planned` in `config/regions.yaml` to planning-ready.
Every step can be repeated safely. The readiness page (Data → Region readiness, or
`GET /api/v1/readiness/<region>`) shows what's still missing after each step.

**Registered and planned today:** Maharashtra (state), Delhi NCR, Bengaluru, Chennai,
Hyderabad, Kolkata, Ahmedabad.

**Onboarded:** `pmr` and `mumbai_pune`.

---

## 0. Before you start

- **Disk and memory.** A metro needs a few GB of database and a routing graph; a whole
  state needs far more. See docs/RUNNING.md section 9 before onboarding a state-sized
  region.
- **Source extract.** Download a Geofabrik extract that covers the region, e.g.
  `https://download.geofabrik.de/asia/india/western-zone-latest.osm.pbf` for
  Maharashtra and Gujarat. Put it in `data/osm/`.

## 1. Complete the registry entry

In `config/regions.yaml`, the entry needs:
- `name` and `bbox` (already there for the planned regions);
- `rtos`: the RTO codes whose registrations cover the region. Source them from the
  state transport department. **Never guess them.**
- `default_area`: the OSM relation id of the main district (used by the Area planner).

Add the RTOs' catchments to `config/demand/allocation.yaml` (`rto_catchments`): which
districts each RTO serves.

## 2. Vehicle registrations

Demand needs registrations for those RTOs:
- **Real data (preferred):** export from the VAHAN dashboard to the CSV format in
  `backend/app/providers/government/vahan.py`, then
  `docker compose exec api python -m app.ingestion.cli vahan <csv>`.
- **Demonstration only:** extend `SYNTHETIC_RTOS` in `backend/app/ingestion/synthetic.py`.
  Every figure built on synthetic data is labelled synthetic with confidence NONE.

## 3. Run the onboarding script

```bash
make onboard-region REGION=bengaluru PBF=/data/osm/southern-zone-latest.osm.pbf DRY=1   # plan
make onboard-region REGION=bengaluru PBF=/data/osm/southern-zone-latest.osm.pbf         # run
```

It runs these steps:

1. **Check** the registry entry and the source extract.
2. **Extract** the region's bounding box and its admin boundaries (levels 4–6) from
   the source extract.
3. **Merge** with the other onboarded regions' extracts
   (`data/osm/onboarded_merged.osm.pbf`).
4. **Import OSM** from the merged extract: roads, POIs, grid assets, land cover,
   boundaries and the place-name gazetteer.
5. **Population:** WorldPop on H3 for the region.
6. **Registrations check:** stops before demand if there are none.
7. **Demand:** forecast, allocation and gap.
8. **Chargers:** Open Charge Map for the region (needs `OCM_API_KEY`), then fusion.
9. **Solar:** NASA POWER irradiance at the region centre.
10. **Readiness:** prints the report.

Skip steps with `--skip "import OSM,population"`, for example when rerunning after a
fix.

## 4. Routing graph (by hand)

The routing engine builds its graph from what's in
`infra/docker/valhalla/custom_files/`:

```bash
cp data/osm/onboarded_merged.osm.pbf infra/docker/valhalla/custom_files/
rm infra/docker/valhalla/custom_files/mumbai_pune.osm.pbf   # the old extract
docker compose restart valhalla    # rebuilds tiles; minutes for a metro, longer for a state
```

Readiness shows **Routing graph = 100** once the region centre snaps to the graph.

## 5. Grid, tariffs, operators (as available)

- **DISCOM asset file:** `make ingest-discom CSV=... DISCOM=...` switches Grid Impact
  from proximity-only to headroom.
- **Tariff file for the state:** outside Maharashtra, add `config/tariffs/<state>.yaml`
  and point the energy and finance code at it. Only Maharashtra is modelled today.
- **Operator OCPI feeds:** these enable calibration.

## 6. Review and flip the status

Check the readiness report. It needs **no blockers** (roads, routing, population,
demand) and a score you're comfortable with. Then set `status: onboarded` in
`config/regions.yaml`. Scenarios, planning runs, the assistant and region-wide
scenarios will now accept the region.

## What readiness measures

| Dimension | Weight | Full marks when |
|---|---|---|
| Roads & POIs | 0.14 | ≥ 1,000 road segments and ≥ 100 POIs in the bounding box |
| Boundaries | 0.04 | Admin boundaries present |
| Routing graph | 0.12 | Valhalla snaps the region centre |
| Population | 0.10 | Region has H3 cells with population |
| Registrations | 0.16 | Real VAHAN rows (synthetic = 0.25) |
| Demand | 0.10 | A base demand run exists |
| Existing chargers | 0.10 | Stations from ≥ 2 fused sources (1 source = 0.6) |
| Grid data | 0.10 | DISCOM-rated assets (OSM substations only = 0.3) |
| Solar | 0.04 | Irradiance loaded for the region |
| Tariffs & costs | 0.06 | Share of sourced (not illustrative) parameters, Maharashtra only |
| Operator feeds | 0.04 | Stations with observed utilisation |

Grades: A ≥ 80, B ≥ 60, C ≥ 40, D below. Weights and thresholds are in
`config/readiness.yaml`.
