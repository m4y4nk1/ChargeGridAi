"""PV + battery recommendation for a planned site (Section 9.9).

Builds the site's hourly load from its sizing, the solar resource from NASA POWER
(always) and Google Solar (when configured and the site is covered), time-of-day
tariffs for its connection, and runs the dispatch LP for a set of scenarios:

- grid only, on the connection sizing chose (the baseline);
- grid + PV;
- grid + PV + battery;
- for an HT site, PV + battery under the LT limit (saving the HT connection and
  transformer, if storage can carry the peak).

The recommendation is the lowest total annual cost: energy + demand charges +
annualised PV, battery and connection. Its effect on the grid connection and OPEX
is reported against the baseline.

Google Solar results are RESTRICTED_GOOGLE: kept at most 30 days, never shown on the
open-map run page (which gets the NASA/canopy result), marked with the licence.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import Assumptions, load_config
from app.core.settings import get_settings
from app.db.models.demand_inputs import SolarResource
from app.db.models.planning import CandidateSite
from app.db.models.provenance import DatasetSnapshot
from app.db.repositories.api_usage import cost_guard_for
from app.engines.energy.dispatch import (
    EnergyProblem,
    EnergyResult,
    annuity_factor,
    pv_capacity_factor,
    solve_dispatch,
)
from app.engines.grid.load_profile import site_hourly_kw
from app.providers.google.solar import GoogleSolar, SolarInsight
from app.services.site_economics import site_economics
from app.services.tariffs import hourly_prices

METHOD = "dispatch_lp_v1"
REPORT_MONTHS = (4, 7)  # April (dry, sunny) and July (monsoon)
_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="energy")


class EnergyError(ValueError):
    pass


async def _solar(
    session: AsyncSession,
) -> tuple[list[list[float]], list[list[float]], list[int], DatasetSnapshot]:
    snap = (
        await session.execute(
            select(DatasetSnapshot)
            .where(DatasetSnapshot.source_id == "nasa_power")
            .order_by(DatasetSnapshot.retrieved_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if snap is None:
        raise EnergyError("No solar resource ingested; run `make ingest-solar`")
    rows = (
        (await session.execute(select(SolarResource).where(SolarResource.snapshot_id == snap.id)))
        .scalars()
        .all()
    )
    ghi = [[0.0] * 24 for _ in range(12)]
    temp = [[25.0] * 24 for _ in range(12)]
    days = [0] * 12
    for r in rows:
        ghi[r.month - 1][r.hour] = r.ghi_wm2
        temp[r.month - 1][r.hour] = r.temp_c
        days[r.month - 1] = r.days
    return ghi, temp, days, snap


async def _google_insight(
    session: AsyncSession, lat: float, lng: float
) -> tuple[SolarInsight | None, str]:
    """(insight, note). Never raises: every failure falls back to the open path."""
    settings = get_settings()
    key = settings.google_maps_server_key
    if not settings.google_solar_enabled:
        return None, "Solar from NASA POWER (Google Solar is switched off: GOOGLE_SOLAR_ENABLED)"
    if not key:
        return None, "Google Solar not configured (no GOOGLE_MAPS_SERVER_KEY)"
    try:
        insight = await GoogleSolar(key, cost_guard_for(session)).building_insights(lat, lng)
    except Exception as exc:  # budget refusal, quota, network: fall back, and say why
        from app.providers.google.errors import redact

        return None, redact(f"Google Solar not used: {exc}", key)
    if insight is None:
        return None, "Google Solar has no coverage (or no usable roof) at this site"
    return insight, "Google Solar building insights used for the roof and yield"


def _scenario(
    res: EnergyResult,
    connection: str,
    connection_annual: float,
    connection_capex: float,
    pv_capex: float,
    bess_capex: float,
) -> dict[str, Any]:
    return {
        "status": res.status,
        "connection": connection,
        "pv_kwp": round(res.pv_kwp, 1),
        "battery_kwh": round(res.battery_kwh, 1),
        "battery_kw": round(res.battery_kw, 1),
        "contract_kw": round(res.contract_kw, 1),
        "annual": {
            "energy": round(res.annual_energy_cost),
            "demand": round(res.annual_demand_cost),
            "pv": round(res.annual_pv_cost),
            "battery": round(res.annual_battery_cost),
            "connection": round(connection_annual),
            "total": round(res.annual_total + connection_annual),
            "opex": round(res.annual_energy_cost + res.annual_demand_cost),
        },
        "capex": {
            "pv": round(pv_capex * res.pv_kwp),
            "battery": round(bess_capex),
            "connection": round(connection_capex),
        },
        "grid_kwh": round(res.grid_kwh),
        "pv_generated_kwh": round(res.pv_generated_kwh),
        "pv_used_kwh": round(res.pv_used_kwh),
        "load_kwh": round(res.load_kwh),
        "solar_share": round(res.pv_used_kwh / res.load_kwh, 4) if res.load_kwh else 0.0,
        "curtailed_share": (
            round(1 - res.pv_used_kwh / res.pv_generated_kwh, 4) if res.pv_generated_kwh else 0.0
        ),
        "dispatch": {
            str(m): {k: [round(v, 2) for v in series[m - 1]] for k, series in res.dispatch.items()}
            for m in REPORT_MONTHS
        }
        if res.dispatch
        else {},
    }


def _optimise(inputs: dict[str, Any]) -> dict[str, Any]:
    """CPU part: every scenario's LP, the recommendation and its effect."""
    common = inputs["common"]
    scenarios: dict[str, Any] = {}
    for name, spec in inputs["scenarios"].items():
        res = solve_dispatch(EnergyProblem(**common, **spec["problem"]))
        if res.status != "optimal":
            scenarios[name] = {
                "status": res.status,
                "label": spec["label"],
                "connection": spec["connection"],
            }
            continue
        bess_capex = (
            res.battery_kwh * inputs["bess_inr_per_kwh"]
            + res.battery_kw * inputs["bess_inr_per_kw"]
        )
        scenarios[name] = {
            "label": spec["label"],
            **_scenario(
                res,
                spec["connection"],
                spec["connection_annual"],
                spec["connection_capex"],
                inputs["pv_inr_per_kwp"],
                bess_capex,
            ),
        }
    feasible = {k: v for k, v in scenarios.items() if v.get("status") == "optimal"}
    best = min(feasible, key=lambda k: feasible[k]["annual"]["total"])
    base, rec = feasible["grid_only"], feasible[best]
    effect = {
        "annual_opex_change": rec["annual"]["opex"] - base["annual"]["opex"],
        "annual_total_change": rec["annual"]["total"] - base["annual"]["total"],
        "contract_kw_change": rec["contract_kw"] - base["contract_kw"],
        "connection_before": base["connection"],
        "connection_after": rec["connection"],
        "added_capex": rec["capex"]["pv"]
        + rec["capex"]["battery"]
        + rec["capex"]["connection"]
        - base["capex"]["connection"],
    }
    saving = -effect["annual_opex_change"]
    effect["simple_payback_years"] = (
        round(effect["added_capex"] / saving, 1)
        if saving > 0 and effect["added_capex"] > 0
        else None
    )
    return {"scenarios": scenarios, "recommended": best, "effect": effect}


async def site_energy(session: AsyncSession, optimisation_id: Any, site_id: Any) -> dict[str, Any]:
    """PV/battery recommendation for a site in a plan (cached on its site_config row)."""
    config = await site_economics(session, optimisation_id, site_id, subsidy=False)
    now = datetime.now(UTC)
    if config.sizing.get("energy"):
        cached: dict[str, Any] = config.sizing["energy"]
        google = cached.get("google")
        if google and datetime.fromisoformat(google["expires_at"]) < now:
            # Past the 30-day cache limit: delete the Google content, don't just hide it.
            cached = {
                **cached,
                "google": None,
                "google_note": "Google Solar result expired (30 days)",
            }
            config.sizing = {**config.sizing, "energy": cached}
            await session.commit()
        failed = not google and str(cached.get("google_note", "")).startswith(
            "Google Solar not used:"
        )
        retry_after = cached.get("google_retry_after")
        # A failed Google call (API disabled, quota, network) isn't final: try again
        # after a cool-down instead of keeping the failure forever.
        if not (failed and (retry_after is None or datetime.fromisoformat(retry_after) < now)):
            return cached

    sizing = config.sizing
    a = Assumptions()
    cfg = load_config("energy.yaml")
    fin = load_config("finance.yaml")
    capex_cfg = load_config("costs", "unit_costs.yaml")["capex"]
    tariffs = load_config("tariffs", "maharashtra.yaml")
    site = await session.get(CandidateSite, site_id)
    assert site is not None
    from geoalchemy2.shape import to_shape

    point = to_shape(site.geom)
    ghi, temp, days, snap = await _solar(session)

    pv = cfg["pv"]
    pr = a.get(pv["performance_ratio"], "energy.pv.performance_ratio")
    coeff = a.get(pv["temp_coefficient_per_c"], "energy.pv.temp_coefficient")
    rise = a.get(pv["cell_temp_rise_c"], "energy.pv.cell_temp_rise_c")
    cf = [
        [pv_capacity_factor(ghi[m][h], temp[m][h], pr, coeff, rise) for h in range(24)]
        for m in range(12)
    ]
    nasa_yield = sum(days[m] * sum(cf[m]) for m in range(12))  # AC kWh per kWp per year

    chargers = int(sizing["chargers"])
    bay = a.get(fin["bay_area_sqm_per_charger"], "finance.bay_area_sqm_per_charger")
    canopy_kwp = (
        chargers
        * bay
        * a.get(pv["canopy"]["usable_share_of_bay_area"], "energy.pv.canopy.usable_share")
        * a.get(pv["canopy"]["kwp_per_m2"], "energy.pv.canopy.kwp_per_m2")
    )

    # Load at the meter: the site's daily energy spread by simulated hourly occupancy.
    eff = a.get(fin["charger_efficiency"], "finance.charger_efficiency")
    load = site_hourly_kw(
        sizing["served_kwh_per_day"], sizing["simulation"]["hourly_utilisation"], eff
    )
    design_peak = float(sizing["electrical"]["sanctioned_kw"])

    rate = a.get(fin["discount_rate"], "finance.discount_rate")
    bat = cfg["battery"]
    pv_cost = a.get(capex_cfg["pv_inr_per_kwp"], "costs.capex.pv_inr_per_kwp")
    kwh_cost = a.get(capex_cfg["bess_inr_per_kwh"], "costs.capex.bess_inr_per_kwh")
    kw_cost = a.get(bat["inr_per_kw"], "energy.battery.inr_per_kw")
    pv_om = a.get(pv["om_pct_capex_per_year"], "energy.pv.om")
    bat_om = a.get(bat["om_pct_capex_per_year"], "energy.battery.om")
    af_pv = annuity_factor(rate, int(pv["lifetime_years"]))
    af_bat = annuity_factor(rate, int(bat["lifetime_years"]))
    af_conn = annuity_factor(rate, int(cfg["grid"]["connection_lifetime_years"]))

    unit = load_config("costs", "unit_costs.yaml")["capex"]
    lt_conn = a.get(unit["lt_connection_inr"], "costs.capex.lt_connection_inr")
    ht_conn = a.get(unit["ht_connection_inr"], "costs.capex.ht_connection_inr") + a.get(
        unit["transformer_inr"], "costs.capex.transformer_inr"
    )
    lt_max = a.get(unit["lt_max_sanctioned_kw"], "costs.capex.lt_max_sanctioned_kw")
    pf = float(fin["power_factor"])

    def tariff_for(connection: str) -> dict[str, Any]:
        t = tariffs["ht_ev_tariff" if connection == "HT" else "lt_ev_tariff"]
        return {
            "price": hourly_prices(t, a),
            "demand_charge_kva_month": a.get(
                t["demand_charge_inr_per_kva_month"], f"tariffs.{connection}.demand"
            ),
        }

    common = {
        "load_kw": load,
        "days": days,
        "power_factor": pf,
        "design_peak_kw": design_peak,
        "annual_cost_per_kwp": pv_cost * (af_pv + pv_om),
        "annual_cost_per_kwh": kwh_cost * (af_bat + bat_om),
        "annual_cost_per_kw": kw_cost * (af_bat + bat_om),
        "round_trip_efficiency": a.get(bat["round_trip_efficiency"], "energy.battery.rte"),
        "min_soc": float(bat["min_soc"]),
        "max_soc": float(bat["max_soc"]),
        "peak_duration_h": a.get(bat["peak_duration_hours"], "energy.battery.peak_duration"),
    }

    def build(pv_max: float, profile: list[list[float]]) -> dict[str, Any]:
        base_conn = str(sizing["electrical"]["connection"])
        conn_capex = {"HT": ht_conn, "LT": lt_conn}
        specs: dict[str, Any] = {}

        def add(
            name: str, label: str, conn: str, allow_pv: bool, allow_bat: bool, cap: float | None
        ) -> None:
            specs[name] = {
                "label": label,
                "connection": conn,
                "connection_capex": conn_capex[conn],
                "connection_annual": conn_capex[conn] * af_conn,
                "problem": {
                    **tariff_for(conn),
                    "cf": profile,
                    "pv_max_kwp": pv_max,
                    "allow_pv": allow_pv,
                    "allow_battery": allow_bat,
                    "contract_cap_kw": cap,
                },
            }

        add("grid_only", "Grid only", base_conn, False, False, None)
        add("pv", "Grid + solar", base_conn, True, False, None)
        add("pv_battery", "Grid + solar + battery", base_conn, True, True, None)
        if base_conn == "HT":
            add("pv_battery_lt", "Solar + battery on an LT connection", "LT", True, True, lt_max)
        return {
            "common": common,
            "scenarios": specs,
            "pv_inr_per_kwp": pv_cost,
            "bess_inr_per_kwh": kwh_cost,
            "bess_inr_per_kw": kw_cost,
        }

    loop = asyncio.get_running_loop()
    open_result = await loop.run_in_executor(_THREAD, _optimise, build(canopy_kwp, cf))
    insight, note = await _google_insight(session, point.y, point.x)
    google = None
    if insight is not None:
        scale = (
            insight.specific_yield_kwh_per_kwp
            * a.get(pv["inverter_efficiency"], "energy.pv.inverter")
            / nasa_yield
        )
        g_cf = [[v * scale for v in row] for row in cf]
        google = {
            **await loop.run_in_executor(_THREAD, _optimise, build(insight.max_array_kwp, g_cf)),
            "insight": {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in asdict(insight).items()
            },
            "license_class": "RESTRICTED_GOOGLE",
            "display": "google_canvas_only",
            "expires_at": insight.expires_at.isoformat(),
        }

    energy = {
        "method": METHOD,
        "open": {
            **open_result,
            "pv_limit": {"source": "canopy over charging bays", "kwp": round(canopy_kwp, 1)},
            "solar_source": "nasa_power",
        },
        "google": google,
        "google_note": note,
        "google_retry_after": (now + timedelta(minutes=10)).isoformat()
        if google is None and note.startswith("Google Solar not used:")
        else None,
        "solar": {
            "snapshot_id": str(snap.id),
            "annual_ghi_kwh_m2": round(sum(days[m] * sum(ghi[m]) for m in range(12)) / 1000, 1),
            "specific_yield_kwh_per_kwp": round(nasa_yield),
            "attribution": "NASA LaRC POWER Project",
        },
        "load": {
            "hourly_kw": [round(v, 2) for v in load],
            "design_peak_kw": design_peak,
            "annual_kwh": round(sum(load) * sum(days)),
        },
        "tariffs": {c: tariff_for(c) for c in ("LT", "HT")},
        "placeholders": a.placeholders_in_use,
    }
    # Stored with the site's sizing; the Google part carries its own expiry.
    config.sizing = {**config.sizing, "energy": energy}
    await session.commit()
    return energy
