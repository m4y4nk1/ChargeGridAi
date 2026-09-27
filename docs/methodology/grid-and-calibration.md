# Grid impact, DISCOM data, operator feeds and calibration (Phase 10)

## Grid Impact (module E) — `/runs/:runId/grid`, `GET /grid/impact?run_id=`

For a run's optimised plan:

- **Site load.** Each site's 24-hour profile at the meter is its daily served energy
  (sizing, Phase 7), spread by the simulated hourly occupancy and divided by charger
  efficiency. Values are hourly averages in kW. The plan curve is the sum over sites.
  It is stacked by charger class, and by vehicle segment using each site's energy
  shares.
- **Charger profile:** chargers and kW per class, and sites per host type.
- **Connections:** HT and LT sites, site transformers and connection cost, all from
  sizing (with illustrative unit costs).

**Grid side, proximity only (the default).** Without DISCOM data, each site is paired
with its nearest OSM substation, and the table shows the load the plan would add there
(peak of the summed hourly profiles, and sanctioned kW). Ratings and loadings are
unknown, so there is no utilisation or headroom. The page and the agents label this
"Proximity-only estimate", with confidence LOW (Section 0 rule 5).

**Grid side, DISCOM-backed.** With DISCOM asset data (`make ingest-discom`):

- **Matching.** An LT site draws from the nearest rated distribution transformer
  within 500 m, else the nearest rated substation within 5 km. An HT site takes supply
  from a substation. Distances are illustrative; see `config/grid.yaml`.
- **Per asset:**
  - `capacity = rated_kVA × loading_limit` (per asset, else the default 0.8);
  - `headroom = capacity − base_peak`;
  - added load is the sum of the matched sites' hourly kW ÷ power factor.

  With an hourly base profile, the peak is read off the combined curve. Without one,
  the sites' peak is added at the coincidence factor (1.0, conservative).
- **Upgrades.** An overloaded transformer is replaced with the next standard size
  (IS 1180 ratings) at illustrative ₹/kVA plus a fixed cost. Beyond 2,500 kVA it is
  flagged "new transformer or HT supply". Substations are flagged for a DISCOM system
  study and not costed.
- **Confidence per asset:**
  - HIGH: measured within 90 days, with an hourly profile;
  - MEDIUM: measured within 365 days;
  - LOW: older or undated.

  The page's overall confidence is the lowest across sites. Plans with partial
  coverage show "mixed".
- **Output.** The page switches to utilisation bins (today vs with the plan), upgrade
  cards and an asset table. An evidence row (`grid:plan_peak_kw`) records the result
  with its confidence and DISCOM snapshot.

The load curve doesn't change between modes; only the grid side does. This is
covered by `tests/integration/test_grid_impact_discom.py`, which uses fixture assets
in a rolled-back transaction.

## DISCOM asset files

- **Template:** `data/templates/discom_assets_template.csv`. Columns: asset code,
  type, name, coordinates, voltage, rated kVA, base peak kVA, measurement date,
  optional loading limit, optional `h00..h23` base load.
- **Validation:** rows are validated (type, region, positive rating, a complete
  hourly profile or none, duplicates), and rejects are listed in the snapshot's
  quality report.
- **Reloading:** loading a file replaces that DISCOM's assets.
- **Licence:** RESTRICTED_COMMERCIAL. Only the file's checksum is kept, never the file,
  and assets are never drawn on the open map. The API returns derived values and asset
  codes only.

## Operator feeds (OCPI 2.2.1)

**Configuration.** Operators with a data agreement go in `config/ocpi_operators.yaml`,
with their tokens as `OCPI_TOKEN_<ID>` in `.env`. Each becomes the source
`operator:<id>`, with the licence class the agreement allows (default
RESTRICTED_COMMERCIAL).

**What the client does** (`app/providers/ocpi/client.py`):
- discovers the 2.2.1 endpoints;
- sends `Authorization: Token <base64>`;
- pages with offset/limit and the `Link` header;
- checks the `status_code` 1000 envelope.

**Mapping into the platform:**
- **Locations** → staged charger records → fusion. Operator records rank above OCM and
  below government records.
- **CDRs** → observed sessions. When an operator sends no CDRs, completed sessions are
  used instead. Location ids are matched to fused stations through their source links.
- **Utilisation.** Sessions are rolled up into `station_utilisation_daily` (energy and
  sessions per station per day, Asia/Kolkata).

**Schedule.** Celery beat runs each configured operator daily. Re-ingesting is
idempotent: sessions are upserted by operator, kind and id.

## Calibration (Section 9.2 step 5)

`make calibrate` (weekly in beat once operators are configured):

1. **Observed.** For stations with at least 28 days of sessions in the last 90 days,
   observed kWh/day is total energy ÷ days spanned. Days without sessions inside the
   span count as zero.
2. **Parametric.** The 2SFCA share of served demand in the station's 10-minute
   catchment, from the stored history-scenario cells:
   `Σ served_i · R_j / A_i`, where `R_j = S_j / Σ D_i`.
3. **Residual model.** A LightGBM model of observed − parametric, using these
   features:
   - parametric kWh;
   - charge points and maximum kW;
   - DC share;
   - catchment population and EVs;
   - mean access ratio;
   - host type.

   The calibrated prediction is parametric + residual, floored at 0.
4. **Metrics.** MAE, RMSE, bias and R² are computed out of fold (5-fold) for both the
   parametric and the calibrated model.
5. **MLflow** (experiment `demand-calibration`, UI at http://localhost:5050) records:
   - params and both metric sets;
   - a per-station predictions CSV and feature importance;
   - the model, registered as `demand_residual_lgbm`;
   - the tag `replaces_parametric=false`.
6. **Stored predictions.** Out-of-fold predictions are stored in `station_prediction`,
   next to the parametric value and the observation.
7. **Too few stations.** With fewer than 30 qualifying stations the run is logged as
   `skipped_insufficient_data` with the counts, and nothing is trained. This is the
   current state: no operator feeds are connected.

The parametric model is never replaced. Calibrated values are reported beside it and
labelled.

## Not yet done

- **Scoring and feasibility still use the proxy.** The grid sub-score and F04 still
  use substation distance. Switching them to headroom needs DISCOM coverage for most
  candidates, since mixing headroom-based and distance-based values in one percentile
  would be meaningless.
- **Feeder-level loading.** Feeders are not matched; only point assets (transformers,
  substations) are.
