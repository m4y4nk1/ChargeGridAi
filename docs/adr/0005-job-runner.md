# 0005: Job runner — Celery now, Temporal later

## Status
Accepted

## Context
ChargeGrid AI has two kinds of background work: (a) scheduled/periodic
ingestion jobs (OSM diffs, OCM polling, raster refreshes) and (b) a
multi-step planning-run chain (`resolve_data → demand → gap → candidates →
feasibility → score → optimise → size → energy → finance → why_not →
report`) that must be resumable, observable step-by-step, and safe to retry
without redoing already-finished steps once runs get long (multi-period
phasing, large regions).

## Decision
Use **Celery + Redis** (with Celery Beat for schedules) for the MVP,
covering both ingestion jobs and the planning-run chain as a Celery chain/
chord. Plan to migrate the **planning-run workflow** to **Temporal** in
Phase 11 (Enterprise) once run complexity (multi-period phasing, longer
chains, cross-region batch runs) outgrows what Celery chains/chords make
easy to reason about and retry.

## Consequences
- Phase 0–10 code depends only on Celery; no Temporal infrastructure is
  provisioned until Phase 11, keeping the local dev stack (docker-compose)
  simpler for longer.
- The planning-run chain must still be designed with idempotent,
  independently-retryable steps and explicit progress events (Redis pub/sub
  → SSE) from day one, so the eventual Temporal migration is a runtime swap
  rather than a redesign.
- Scheduled ingestion jobs (Section 13 of the brief) stay on Celery Beat
  indefinitely — they don't have the multi-step-workflow problems Temporal is
  being brought in to solve.
