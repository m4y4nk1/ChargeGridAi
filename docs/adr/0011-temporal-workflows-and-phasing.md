# 0011: Temporal for long planning workflows (optional), and rolling-horizon phasing

## Status
Accepted (Phase 11)

## Context
The brief asks for two things in Phase 11:

- **Temporal:** migrate long, multi-step workflows to Temporal.
- **Model C phasing:** periods 2026, 2028 and 2030, each with its own budget. Sites
  stay open once built and can be expanded later, and the objective discounts future
  coverage.

A planning run takes minutes. It stops for a human confirmation before optimising,
and a worker killed mid-run (memory) used to leave the run hanging until the Celery
result was reconciled.

## Decision
1. **A job-runner switch.** `JOB_RUNNER=celery|temporal` in `app/services/jobs.py`.
   - Celery stays the default for local development; it's lighter.
   - Staging and prod use Temporal.
   - Task ids record the runner (`temporal:<workflow id>`), so a run is always
     continued and reconciled by the runner that started it.
2. **`PlanningWorkflow`** runs two activities: `plan_to_scoring`, then
   `optimise_and_value`.
   - Each activity has a retry policy (3 attempts) and heartbeats.
   - Data errors (`RunError`, `OptimisationError`, `EconomicsError`) are
     non-retryable.
   - The human confirmation is a durable `confirm` signal, with a 7-day timeout, and
     there is a `waiting_for_confirmation` query.
   - Verified end to end with the compose `temporal` profile: stage 1, then the query
     shows the run waiting, then the signal, then stage 2.
3. **Phasing is solved period by period (rolling horizon).**
   - Each period's MCLP sees that year's unmet demand.
   - Earlier sites stay forced open. They may switch to a larger bundle of the same
     charger class, and only the difference counts against that period's budget
     (`Problem.existing`).
   - The site limit is cumulative.
   - Discounted coverage is reported but not optimised jointly.

## Consequences
- **Phasing is myopic.** An early period can't hold budget back for a later, larger
  gap. The docs and the UI say so. A joint multi-period MIP can replace the loop behind
  the same API if solve times allow.
- **Temporal adds its own server.** Production runs it from its official Helm chart in
  its own namespace. Locally it's an optional compose profile (about 600 MB of memory
  with its worker).
