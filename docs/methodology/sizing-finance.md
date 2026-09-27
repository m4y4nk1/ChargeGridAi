# Charger sizing and site finance (Phase 7)

Implements Sections 9.8 and 9.10 of the brief. The code:

- Sizing: `backend/app/engines/sizing/queueing.py`.
- Finance: `backend/app/engines/finance/project.py`.
- Orchestration: `backend/app/services/site_economics.py`.

Both engines are pure and unit-tested. Configuration lives in:

- `config/sizing/sizing.yaml`
- `config/finance.yaml`
- `config/costs/unit_costs.yaml`
- `config/tariffs/maharashtra.yaml`

Every site in a run's plan is sized and valued in the `economics` step. A
what-if or strategy site is sized and valued the first time someone opens
its Sizing or Finance tab, which takes about a second. Results are cached
in `site_config` for each plan, site and subsidy setting.

> **Illustrative.** Demand is synthetic. The following are all round-number
> placeholders, flagged in the UI:
>
> - charger prices;
> - tariffs;
> - selling price;
> - taper factors;
> - arrival curves;
> - the uncertainty ranges.
>
> The outputs show how the method works. They are not an investment case.

## 1. Sizing (Section 9.8)

1. **Sessions.** Start from the kWh/day the plan assigns to the site. Split
   it by vehicle segment, in proportion to each segment's public kWh in the
   site's catchment (EV stock × km × kWh/km × public share, from Phase 3).
   Only segments that use the charger type count; for DC that is 4-wheelers,
   buses and LCVs. Each segment's kWh per session then gives its session
   count.
2. **Hourly profile.** Each segment has an illustrative 24-hour curve:
   private cars peak in the evening, fleets around shift changes, commercial
   vehicles at midday. The curve is multiplied by a host-type modifier, for
   example mall evenings or office mornings.
3. **Session duration.**
   `kWh ÷ min(charger kW × taper, vehicle max kW) + 5 min overhead`. A
   120 kW charger still charges a 50 kW car at 50 kW.
4. **Erlang-C.** For the peak hour, pick the smallest charger count *c* that
   meets both targets:
   - P(wait > 10 min) ≤ 10% (the brief's target);
   - peak occupancy ≤ 85%.

   It uses a numerically stable Erlang-B recursion.
5. **SimPy validation.** Simulate 1,000 days as a continuous-time M(t)/G/c
   FIFO queue:
   - arrivals are Poisson hour by hour;
   - service times are gamma-distributed with CV 0.5, which is less variable
     than Erlang's exponential assumption;
   - queues carry over from one hour to the next.

   The simulation reports P(wait > 10 min) overall and in the peak hour,
   the mean and 95th-percentile wait, and daily and hourly utilisation.
   Sites are flagged if simulated utilisation falls outside the 10–45% band,
   or if the simulation misses a target that Erlang-C met.
6. **Connectors.** Each segment has a connector mix (CCS2, Bharat DC-001,
   CHAdeMO, LECCS, Type 2 AC). The site's mix is spread over its guns
   (2 per DC charger) by largest remainder.
7. **Electrical.**
   - Sanctioned load = Σ kW × 0.8 diversity.
   - Above 150 kW, the site needs an HT (11 kV) connection and its own
     transformer.
   - The connection cost comes from `unit_costs.yaml`.

**Checks** (`tests/unit/test_sizing.py`):

- Erlang-C matches textbook values; for example, M/M/2 at 1 Erlang gives
  P(wait) = 1/3.
- For a stationary M/M/c queue, the SimPy simulation agrees with Erlang-C
  within 2 percentage points on P(wait > t) and on utilisation.
- Runs are seeded, so results reproduce exactly.

## 2. Finance (Section 9.10)

- **CAPEX:**
  - chargers (the sized count);
  - installation (10%);
  - civil works and canopy;
  - software;
  - LT or HT connection, plus a transformer for HT;
  - contingency (10%);
  - land-lease deposit (35 m² per charger).
- **Subsidy.** A PM E-DRIVE-style subsidy is a switch on the Finance tab and
  is off by default: eligibility is never assumed. When on, it covers 80%
  (illustrative) of the grid-connection and transformer cost.
- **Energy sold.** Year *t* sells
  `min(capacity, served kWh/day × ramp_t × growth) × 365`:
  - the ramp is 35 / 60 / 80 / 100% over the first four years, then demand
    grows 8% a year;
  - capacity is chargers × effective kW × 24 h × 50% maximum practical
    utilisation.
- **Revenue** is energy × selling price (₹20/kWh DC, illustrative).
- **OPEX:**
  - grid energy = energy sold ÷ 93% charger efficiency × tariff (₹8.0 LT,
    ₹7.5 HT per kWh);
  - demand charges on sanctioned kVA (₹75 LT, ₹350 HT per kVA per month);
  - maintenance at 3% of CAPEX per year and insurance at 0.5%;
  - rent or revenue share (10%), roaming fees (2%) and payment processing
    (2%);
  - staff (₹3 lakh a year).
- **Outputs.** Pre-tax project cash flows with no debt, over 10 years at a
  12% discount rate, giving NPV, IRR and payback.

**Uncertainty:**

- **Monte Carlo.** 5,000 draws of five independent triangular multipliers:
  - demand 0.6–1.0–1.3;
  - ramp speed 0.7–1.0–1.15;
  - selling price 0.85–1.0–1.1;
  - CAPEX 0.9–1.0–1.3;
  - grid energy 0.9–1.0–1.25.

  Outputs are P10/P50/P90 of NPV, IRR, payback, year-5 EBITDA and
  steady-state monthly revenue, plus P(NPV > 0). A draw that never pays back
  counts as the worst outcome rather than being dropped, so "P90 payback:
  never" is a real answer.
- **Tornado.** Each input is moved to its low and high end while the others
  stay at base.
- **Three named cases.** Pessimistic, base and optimistic, with every input
  at its adverse, central or favourable end at the same time. This is
  harsher than a Monte Carlo P10, which draws inputs independently.

## 3. Result: headline demo (Pune, 2028, ₹10 Cr plan, 8 sites; 26 Sep 2026)

- **Sizing takes more chargers than the plan's bundles.** 52 chargers
  across 8 sites, against the plan's 28:
  - The optimiser's capacity rule (rated kW × 24 h × 25% utilisation)
    assumes a 120 kW charger delivers 120 kW.
  - Most DC sessions are cars that accept about 50 kW, so a 120 kW charger
    delivers roughly half its rating.
  - Example: Mangalwar Peth serves 2,736 kWh/day, which is 107 sessions of
    25.6 kWh and 34.5 minutes each. It needs 7 × DC 120 kW, not 4:

    | Metric | Result |
    |---|---|
    | Simulated P(wait > 10 min), peak hour | 3.5% (Erlang-C: 5.0%) |
    | Mean wait | 0.4 min |
    | Daily utilisation | 37% |
    | Sanctioned load | 672 kW (HT connection with transformer) |
    | Guns | 12 × CCS2 + 2 × Bharat DC-001 |

  - This is a reason to replace the optimiser's flat capacity rule with the
    sized capacity; see known limits below.
- **Finance for that site:**
  - CAPEX ₹2.29 Cr.
  - Base case: NPV +₹0.30 Cr, IRR 14%, payback 6.2 years.
  - Monte Carlo P50: NPV −₹0.56 Cr, with a 28% chance NPV > 0. The
    uncertainty ranges lean downside, with demand as low as 0.6× and CAPEX
    as high as 1.3×.
  - Demand and selling price move NPV most, then grid energy price, CAPEX
    and ramp speed.
- **Portfolio.** Across the 8 sites, sized CAPEX totals ₹16.6 Cr and the
  base-case NPVs sum to about zero. No site is positive at P50 under these
  placeholder prices, so the plan's ₹10 Cr budget buys less capacity than
  the optimiser assumed.

## 4. Known limits

- **The optimiser and sizing disagree on capacity.** The optimiser uses a
  flat utilisation rule; sizing uses queueing. Feeding sized capacity back
  into Model A would make plans consistent, but the optimisation becomes
  non-linear. Candidates for doing that: a per-bundle effective capacity, or
  one sizing pass followed by a re-solve.
- **Tariffs are flat.** There are no time-of-day slabs yet. Phase 8 (energy
  dispatch) adds them, along with solar and battery storage.
- **Finance is simplified.** No tax, depreciation or debt; project-level
  cash flows only.
- **Arrival curves and host modifiers are illustrative.** Calibrate them
  against operator or OCPI session data (Phase 10).
