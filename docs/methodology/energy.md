# Solar + battery: resource, dispatch and recommendation (Phase 8)

This note implements Section 9.9 of the brief.

**Code:**
- `backend/app/engines/energy/dispatch.py`: the dispatch model (pure, unit-tested).
- `backend/app/services/site_energy.py`: runs it for a site.
- `backend/app/providers/nasa/power.py`: fetches the NASA POWER solar data.
- `backend/app/providers/google/solar.py`: calls the Google Solar API.

**Config:**
- `config/energy.yaml`
- `config/tariffs/maharashtra.yaml` (including the time-of-day slabs)
- `config/costs/unit_costs.yaml` (PV and battery prices)

**Where it shows:** the **Energy** tab of any site in a plan, via
`GET /optimisations/{id}/sites/{site_id}/energy`. It is computed on first
request, which takes about 0.2 s, and cached with the site's sizing.

> **Illustrative.** Demand is synthetic. PV and battery prices, time-of-day
> slabs, performance ratio, battery efficiency and the peak duration are all
> placeholders, and the tab says so. Acceptance criterion: *"PV/BESS
> recommendation with effect on grid connection and OPEX."*

## 1. Solar resource

- **NASA POWER is the default, and the fallback.**
  - `make ingest-solar` fetches a year of hourly global horizontal irradiance
    and air temperature (2024, local solar time) at the Pune region centre.
  - It reduces them to **12 representative days**: the mean profile for each
    hour of the day, per month, weighted by that month's days.
  - One point stands for every site, because the resource varies little over
    the region. For 2024 the annual irradiance is 1,778 kWh/m², giving about
    1,263 kWh per kWp per year after losses.
  - Each ingest is one dataset snapshot with an OPEN licence; the raw JSON is
    kept in the raw lake.
- **PV output per kWp:**
  - `GHI/1000 × performance ratio (0.78) × (1 − 0.4%/°C × (T_cell − 25))`;
  - `T_cell = T_air + GHI/800 × 25 °C`.
- **PV limit without Google:** a canopy over the charging bays, equal to
  chargers × 35 m² × 80% usable × 0.2 kWp/m². Host-building roofs aren't
  mapped yet.
- **Google Solar API, when configured.**
  - `buildingInsights:findClosest` returns the host building's maximum array
    (kWp) and its modelled yearly DC energy. The roof becomes the PV limit,
    and the NASA hourly shape is rescaled so the annual yield matches
    Google's.
  - Solar API returns **NOT_FOUND where it has no coverage**, or the building
    may be more than 150 m from the site. Either way the site falls back to
    NASA plus the canopy, and the tab says why.
  - Only Building Insights is called. Data Layers, at $30 per 1,000 calls, is
    never used.
  - Every call goes through the cost guard, which **refuses** calls until
    all of these are set:
    - `GOOGLE_MAPS_SERVER_KEY`;
    - `COST_CAP_MONTHLY_INR_GOOGLE`;
    - the INR/USD rate in `config/costs/api_pricing.yaml`.
- **Licence for Google results (RESTRICTED_GOOGLE):**
  - kept for at most 30 days, then deleted;
  - never written to the raw lake;
  - not shown on the open-map run page. The API reports only that a Google
    result exists (see ADR 0008).
- **`make solar-coverage`** samples the latest run's 20 top-ranked sites and
  reports Google's coverage share and imagery quality. It closes
  `docs/verification.md` item 3 once a key exists.

## 2. Dispatch LP

The model runs over **12 representative days × 24 hours**.

| Decision | |
|---|---|
| `kWp` | PV size, up to the limit |
| `kWh`, `kW` | battery energy and power |
| `contract_kw` | grid contract demand |
| per hour | solar used, grid, battery charge and discharge, state of charge |

It **minimises** the yearly total:

- energy bought, at each hour's time-of-day price;
- demand charges: ₹/kVA-month × 12 × contract ÷ power factor;
- annualised PV and battery CAPEX, at the finance discount rate over 25 and
  10 years, plus O&M.

Subject to:

- **Hourly balance:** solar + grid + battery discharge = load + battery
  charge. Unused solar is curtailed; the site doesn't export.
- **Battery state of charge:** it carries from hour to hour, wraps around
  each day, and stays between 10% and 95%.
- **Charge and discharge rate:** each is at most the battery's kW.
- **Grid draw:** at most the contract in every hour.
- **Short peaks:** contract + battery kW ≥ the design peak, which is the
  sizing's sanctioned load. Hourly averages can't see the minutes when every
  charger draws at once, so that peak must be covered. The battery's usable
  energy must also last the peak's 30 minutes. A unit test caught the
  version without this rule, which "covered" the peak with a battery holding
  no energy.

**The load** is the site's sized kWh/day, spread across the day by its
simulated hourly occupancy (Phase 7) and divided by 93% charger efficiency.

## 3. Options and recommendation

| Option | What it tests |
|---|---|
| Grid only | The baseline, on the connection that sizing chose |
| Grid + solar | Whether solar pays for itself on energy cost alone |
| Grid + solar + battery | Battery arbitrage plus trimming the contract |
| Solar + battery on an LT connection | HT sites only: whether storage can hold the site under the 150 kW LT limit, saving the HT connection and transformer and the higher HT demand charge |

The recommended option has the **lowest total annual cost**. That total
includes each option's grid connection, annualised over 20 years. The
effect is reported against the baseline:

- connection type;
- contract kW;
- annual OPEX (energy + demand charges);
- added CAPEX;
- simple payback.

## 4. Result: headline demo sites (Pune, 2028; 26 Sep 2026)

**Six HT sites** each have 7 × DC 120 kW chargers and 2,736 kWh/day (about
1.08 GWh a year), with a 672 kW design peak.

| | Grid only | Recommended: solar + battery on LT |
|---|---|---|
| Connection | HT, 672 kW contract | LT, 150 kW contract |
| Equipment | — | 39 kWp solar canopy + 307 kWh / 522 kW battery |
| OPEX | ₹111.6 L/yr | ₹83.0 L/yr (**−₹28.6 L/yr**) |
| Added CAPEX | — | ₹1.13 Cr, simple payback 4.0 years |
| Total annual cost (incl. annualised equipment) | — | −₹5.9 L/yr |

Notes:

- **The saving is mostly demand charges.** The battery covers the short
  peaks, so the site avoids ₹350/kVA-month on 672 kVA, plus the HT
  connection and transformer.
- **The battery also buys energy on the night rebate** and discharges into
  the evening peak.
- **Solar supplies only 5%**, because the canopy is small (39 kWp) against
  about 1 GWh a year of load. A Google Solar roof would allow a larger array.

**The other two sites:**

- The busiest site (9 chargers) stays on HT, with its contract cut from
  672 to 227 kW, saving ₹35.2 L/yr on OPEX.
- The small LT site gets solar only, 16.8 kWp, saving ₹1.8 L/yr.

## 5. Known limits

- **Representative days average out the weather.** A run of cloudy monsoon
  days, or the single busiest day, isn't modelled. The battery rule covers
  short peaks, not multi-day ones.
- **Whether a battery can legally keep an HT-sized site on LT** depends on
  the DISCOM's sanctioned-load rules. The model assumes the contract, not
  the connected load, sets the connection type. Check this before relying
  on the LT option.
- **No export or net metering.** No battery degradation beyond its 10-year
  life. No time-varying demand from the charging ramp: the load is
  steady-state.
- **Finance hasn't absorbed the recommendation yet.** Finance (Phase 7)
  still values the grid-only site, although it now uses time-of-day-weighted
  energy prices. Feeding the recommended option's OPEX and CAPEX into the
  cash flows is a follow-up.
- **Pyomo isn't used.** The brief names Pyomo + HiGHS, but the LP runs on
  OR-Tools' GLOP, already a dependency (ADR 0008). The formulation is plain
  linear, so moving it to Pyomo is mechanical.
