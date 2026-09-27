# 0010: Contractual grid and operator data, and a calibration that never replaces the model

## Status
Accepted (Phase 10)

## Context
Phase 10 brings in real-world data that comes only through agreements:

- **DISCOM data:** transformer and substation ratings and loadings;
- **Operator data:** OCPI locations, sessions and CDRs.

Neither is available yet. The brief also requires:

- never pretending to know grid headroom (Section 0 rule 5);
- a calibration hook that trains a LightGBM residual model, logs it in MLflow, and
  never silently replaces the parametric model (Section 9.2 step 5).

## Decision
1. **Contractual data is its own licence tier in practice.**
   - DISCOM files and operator feeds default to `RESTRICTED_COMMERCIAL`. Their raw
     payloads are checksummed but never stored in the raw lake, and they never reach
     the open map.
   - An operator whose agreement allows republication can be set to `OPEN` in
     `config/ocpi_operators.yaml`.
2. **Grid Impact runs in three modes**, chosen per site: `proxy`, `discom` or
   `mixed`.
   - The proxy mode shows no utilisation or headroom at all, only the load added at
     the nearest mapped substation, with confidence LOW.
   - Confidence in DISCOM mode depends on how old the measurement is and whether an
     hourly profile exists.
   - Substation augmentation is flagged, not costed.
3. **Headroom follows the brief's formula** (rated × loading limit − base peak). The
   plan's load adds hour by hour when a base profile exists, and at a configurable
   coincidence factor (default 1.0) otherwise.
4. **OCPI 2.2.1 client in-house** (httpx, about 200 lines) rather than a library.
   Only the receiver-side pull of `locations`, `sessions` and `cdrs` is needed.
   Operators become ordinary charger sources (`operator:<id>`) in the existing fusion
   (ADR 0006).
5. **Calibration trains on residuals and is logged side by side.**
   - LightGBM learns observed − parametric, with out-of-fold metrics for both models.
   - MLflow records both metric sets and registers the residual model, tagged
     `replaces_parametric=false`.
   - Predictions are stored beside the parametric value.
   - Below a minimum sample (30 stations with 28+ days) the run is logged as skipped.
   - The backend uses `mlflow-skinny` pinned to the tracking server's version (2.17.2)
     to keep the image small.

## Consequences
- **Acceptance can't be shown on real data yet.** The switch from proxy to
  data-backed, with a confidence change, is demonstrated with fixture DISCOM assets in
  a rolled-back integration test. Calibration logs a "skipped" run until operators are
  connected. Both paths run unchanged once data arrives.
- **Scoring and F04 still use the proximity proxy**; see
  `docs/methodology/grid-and-calibration.md`.
- **Backend image dependencies.** It gains `libgomp1` and LightGBM, and
  `mlflow-skinny` pinned to 2.17.2.
