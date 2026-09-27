# 0007: Race a greedy plan against the MIP; build the Pareto front from exact solves

## Status
Accepted (Phase 6)

## Context
Section 9.7 of the brief sets four requirements for optimisation:

- Model A is a capacitated maximal covering MIP on OR-Tools.
- What-ifs re-solve in under 10 s with warm starts.
- The Pareto front (Model B) comes from pymoo NSGA-II.
- Every run stores its solver stats.

The Pune problem size:

- 200 candidate sites × 4 charger bundles;
- about 37,000 demand-assignment variables;
- about 3,700 spacing pairs.

Benchmarks on the 3.8 GB Docker VM:

- HiGHS and SCIP solve it in 4–35 s, with high variance.
- Harder what-ifs (₹20 Cr, adoption +30%) sometimes timed out at 8 s with
  no incumbent (HiGHS) or a 44% gap (SCIP).
- Warm starts don't work through OR-Tools' `pywraplp` in this setup:
  - HiGHS **segfaults** when given a hint (OR-Tools 9.15, HiGHS 1.12).
  - SCIP accepted the hint but didn't use it: given the known optimum as
    a hint, it still returned a solution 25% worse.
- Worker memory: each solve peaks at around 300 MB. Solving on the default
  thread pool got the worker OOM-killed, because each thread kept its own
  glibc heap arena.

## Decision
1. **Race a greedy plan against the MIP** (`solve_best`).
   - A greedy heuristic adds the (site, bundle) with the best marginal
     value per rupee. It respects budget, site limit, spacing, and forced
     or excluded sites.
   - Its plan is evaluated **exactly**: an LP allocates demand optimally
     on the chosen sites.
   - The MIP then runs for the rest of the time limit, and the better of
     the two is returned.
   - The MIP's best bound is kept only when it is valid (≥ the incumbent).
   - Status is reported as:
     - `optimal`: within the MIP gap;
     - `feasible`: a proven gap exists;
     - `heuristic`: no bound available.
   - On Pune, the greedy plan comes within 0.03% of the proven optimum in
     about 0.03 s.
2. **Lossless zone aggregation.** Cells covered by exactly the same set of
   sites merge into one zone, and results are split back pro rata to
   demand.
3. **HiGHS** is the default solver; it never receives hints. SCIP remains
   selectable in `config/optimisation.yaml`.
4. **Pareto front from exact solves, not NSGA-II.** Each named strategy
   (maximum coverage, equity, cost optimised, commercial return) is
   solved at 50/100/150/200% of the budget. The non-dominated points on
   (coverage ↑, cost ↓, equity ↑) are flagged `on_front`. The named
   strategies are then real points on the front, which is what module C
   needs.
5. **Memory.**
   - All solves run on one dedicated solver thread.
   - `MALLOC_ARENA_MAX=2` is set for the backend containers.
   - The worker runs 2 processes, recycled after each task.
   - Each run step drops its ORM objects before the next step loads its
     own.

## Consequences
- What-ifs finish in about 6.5 s end to end on Pune. When the MIP can't
  finish in time, the answer is flagged `heuristic` rather than dropped.
- Front quality is limited by the grid (4 strategies × 4 budget shares)
  rather than by a genetic search. Exact points are reproducible run to
  run; NSGA-II's are not. If a denser front is needed, add budget shares
  or strategies.
- pymoo is not a dependency. Revisit once:
  - the front needs objectives that the MIP can't express linearly, such
    as queueing-based utilisation from Phase 7; or
  - the candidate pool outgrows exact solves.
- Why-not (15 re-solves at 2 s each) and the strategy grid take about
  2 minutes per run. That's acceptable for a background job, but it is
  why the grid is kept small.
