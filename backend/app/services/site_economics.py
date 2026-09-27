"""Sizing (Section 9.8) and finance (Section 9.10) for a planned site.

For an optimisation's site: the kWh/day the plan assigns it, split by vehicle segment
from the EV stock in its catchment (only segments that use the charger type), become
sessions and an hourly arrival profile. Erlang-C picks the charger count, SimPy
checks it, and the sized configuration is costed and valued over the horizon with a
Monte Carlo, a tornado and three named cases. Results are cached per optimisation,
site and subsidy setting in `site_config`, with an evidence row each for the charger
count and P50 NPV.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import Assumptions, load_config
from app.db.models.planning import (
    CandidateSite,
    Optimisation,
    OptimisationSite,
    PlanningRun,
    SiteConfig,
)
from app.db.models.provenance import Evidence
from app.db.repositories.demand import supply_assumptions
from app.engines.demand.charging_demand import SegmentUsage, public_kwh_per_day
from app.engines.finance.project import (
    FinanceInputs,
    Triangular,
    cases,
    cash_flows,
    monte_carlo,
    tornado,
)
from app.engines.optimisation.mclp import bundle, charger_kw
from app.engines.sizing.queueing import (
    SegmentDemand,
    allocate_guns,
    choose_chargers,
    connector_mix,
    hourly_profile,
    session_minutes,
    simulate,
)
from app.services.optimisation_run import _costs
from app.services.tariffs import hourly_prices, load_weighted_price

METHOD_SIZING = "sizing_erlang_c_simpy_v1"
METHOD_FINANCE = "finance_mc_v1"

# CPU-bound; one thread keeps memory flat (see optimisation_run).
_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="economics")


class EconomicsError(ValueError):
    pass


def charger_type(charger_class: str) -> str:
    return "DC" if charger_class.upper().startswith("DC") else "AC"


def _usage(a: Assumptions) -> dict[str, SegmentUsage]:
    cfg = load_config("demand", "segments.yaml")["segments"]
    out = {}
    for seg, c in cfg.items():
        k = f"segments.{seg}"
        out[seg] = SegmentUsage(
            annual_km=a.get(c["annual_km"], f"{k}.annual_km"),
            kwh_per_km=a.get(c["kwh_per_km"], f"{k}.kwh_per_km"),
            public_share=a.get(c["public_charging_share"], f"{k}.public_charging_share"),
            kwh_per_public_session=a.get(
                c["kwh_per_public_session"], f"{k}.kwh_per_public_session"
            ),
        )
    return out


_STOCK_SQL = text(
    "select f.ev_stock_by_segment from site_catchment c "
    "join h3_cell_feature f on f.h3::text = any(c.h3_cells) "
    "where c.site_id = :site and f.scenario = :scenario and f.year = :year "
    "and f.ev_stock_by_segment is not null"
)


async def _segment_kwh_shares(
    session: AsyncSession,
    site_id: uuid.UUID,
    year: int,
    demand_scenario: str,
    ctype: str,
    usage: dict[str, SegmentUsage],
    sizing_cfg: dict[str, Any],
) -> dict[str, float]:
    """Share of the catchment's public kWh from each segment that uses `ctype`."""
    stock: dict[str, float] = {}
    for (row,) in (
        await session.execute(
            _STOCK_SQL, {"site": site_id, "scenario": demand_scenario, "year": year}
        )
    ).all():
        for seg, band in row.items():
            # Stored as [P10, P50, P90] per segment (Phase 3); sizing uses the P50.
            p50 = band[1] if isinstance(band, list) else band
            stock[seg] = stock.get(seg, 0.0) + float(p50)
    uses = {s for s, c in sizing_cfg["segments"].items() if ctype in c["uses"]}
    kwh = {
        seg: float(public_kwh_per_day(n, usage[seg]))
        for seg, n in stock.items()
        if seg in uses and seg in usage and n > 0
    }
    total = sum(kwh.values())
    if total <= 0:
        raise EconomicsError(f"No {ctype}-charging demand in this site's catchment")
    return {seg: v / total for seg, v in kwh.items()}


def _compute(
    served_kwh: float,
    charger_class: str,
    host_type: str,
    shares: dict[str, float],
    usage: dict[str, SegmentUsage],
    a: Assumptions,
    subsidy: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The CPU-heavy part: sizing, simulation, CAPEX, cash flows and uncertainty."""
    sz = load_config("sizing", "sizing.yaml")
    fin = load_config("finance.yaml")
    ctype = charger_type(charger_class)
    kw = charger_kw(charger_class)
    taper = a.get(sz["charging_curve_taper_factor"][charger_class], f"sizing.taper.{charger_class}")
    overhead = a.get(sz["plug_in_overhead_minutes"], "sizing.plug_in_overhead_minutes")

    segments, service = [], []
    for seg, share in shares.items():
        seg_cfg = sz["segments"][seg]
        max_kw = a.get(seg_cfg["max_kw"], f"sizing.segments.{seg}.max_kw")
        u = usage[seg]
        segments.append(
            SegmentDemand(
                name=seg,
                kwh_per_day=served_kwh * share,
                kwh_per_session=u.kwh_per_public_session,
                max_kw=max_kw,
                curve=sz["arrival_curves"][sz["segment_curve"][seg]],
            )
        )
        service.append(session_minutes(u.kwh_per_public_session, kw, taper, max_kw, overhead))
    hourly, sessions = hourly_profile(segments, sz["host_type_arrival_modifier"].get(host_type))
    session_share = sessions / sessions.sum()
    mean_service = float((session_share * service).sum())
    peak_hour = int(hourly.argmax())

    targets = sz["service_targets"]
    max_wait = float(targets["max_wait_minutes"])
    choice = choose_chargers(
        float(hourly[peak_hour]),
        mean_service,
        max_wait,
        float(targets["max_prob_wait"]),
        a.get(targets["max_peak_utilisation"], "sizing.max_peak_utilisation"),
    )
    sim_cfg = sz["simulation"]
    sim = simulate(
        hourly.tolist(),
        session_share.tolist(),
        service,
        choice.chargers,
        int(sim_cfg["simulated_days"]),
        a.get(sim_cfg["service_time_cv"], "sizing.service_time_cv"),
        max_wait,
        int(sim_cfg["seed"]),
    )
    band = targets["utilisation_band"]
    band_min = a.get(band["min"], "sizing.utilisation_band.min")
    band_max = a.get(band["max"], "sizing.utilisation_band.max")

    mix = connector_mix(
        {s.name: float(n) for s, n in zip(segments, sessions, strict=True)},
        {s: sz["segments"][s]["connectors"] for s in shares},
    )
    guns = choice.chargers * int(sz["guns_per_charger"][ctype])

    supply_a, _ = supply_assumptions()
    costs = _costs(a)
    b = bundle(
        charger_class,
        choice.chargers,
        costs,
        24 * supply_a.availability * supply_a.target_utilisation,
    )
    capex_cfg = load_config("costs", "unit_costs.yaml")["capex"]
    bay = a.get(fin["bay_area_sqm_per_charger"], "finance.bay_area_sqm_per_charger")
    lease = a.get(
        capex_cfg["land_lease_deposit_inr_per_sqm"], "costs.capex.land_lease_deposit_inr_per_sqm"
    )
    capex_lines = {**b.cost_breakdown, "land_lease_deposit": choice.chargers * bay * lease}
    if subsidy:
        share = a.get(fin["subsidy"]["upstream_share"], "finance.subsidy.upstream_share")
        capex_lines["subsidy_upstream"] = -share * (
            capex_lines["connection"] + capex_lines["transformer"]
        )

    sizing = {
        "method": METHOD_SIZING,
        "charger_class": charger_class,
        "chargers": choice.chargers,
        "total_kw": choice.chargers * kw,
        "served_kwh_per_day": round(served_kwh, 1),
        "sessions_per_day": round(float(sessions.sum()), 1),
        "mean_kwh_per_session": round(served_kwh / float(sessions.sum()), 2),
        "mean_service_minutes": round(mean_service, 1),
        "segments": [
            {
                "segment": s.name,
                "kwh_share": round(shares[s.name], 4),
                "session_share": round(float(sh), 4),
                "kwh_per_session": s.kwh_per_session,
                "service_minutes": round(m, 1),
                "effective_kw": round(min(kw * taper, s.max_kw), 1),
            }
            for s, sh, m in zip(segments, session_share, service, strict=True)
        ],
        "hourly_arrivals": [round(float(h), 3) for h in hourly],
        "peak_hour": peak_hour,
        "erlang": {
            "peak_arrivals_per_hour": round(float(hourly[peak_hour]), 2),
            "prob_wait_over": round(choice.prob_wait_over, 4),
            "mean_wait_min": round(choice.mean_wait_min, 2),
            "peak_utilisation": round(choice.peak_utilisation, 4),
            "capped": choice.capped,
        },
        "simulation": {k: v for k, v in asdict(sim).items()},
        "targets": {
            "max_wait_minutes": max_wait,
            "max_prob_wait": float(targets["max_prob_wait"]),
            "utilisation_band": [band_min, band_max],
        },
        "flags": {
            "oversized": sim.utilisation < band_min,
            "busy": sim.utilisation > band_max,
            "misses_wait_target_in_simulation": sim.prob_wait_over_peak_hour
            > float(targets["max_prob_wait"]),
        },
        "connectors": {
            "session_share": {k: round(v, 4) for k, v in mix.items()},
            "guns": allocate_guns(mix, guns),
            "guns_per_charger": int(sz["guns_per_charger"][ctype]),
        },
        "electrical": {
            "sanctioned_kw": b.sanctioned_kw,
            "diversity_factor": costs.diversity_factor,
            "connection": b.connection,
            "transformer_required": b.connection == "HT",
            "lt_max_sanctioned_kw": costs.lt_max_sanctioned_kw,
            "connection_cost_inr": b.cost_breakdown["connection"] + b.cost_breakdown["transformer"],
        },
    }

    # --- finance
    opex = load_config("costs", "unit_costs.yaml")["opex"]
    tariffs = load_config("tariffs", "maharashtra.yaml")
    tariff = tariffs["ht_ev_tariff" if b.connection == "HT" else "lt_ev_tariff"]
    effective_kw = sum(
        sh * min(kw * taper, s.max_kw) for s, sh in zip(segments, session_share, strict=True)
    )
    max_util = a.get(fin["max_practical_utilisation"], "finance.max_practical_utilisation")

    def o(key: str) -> float:
        return a.get(opex[key], f"costs.opex.{key}")

    inputs = FinanceInputs(
        served_kwh_per_day=served_kwh,
        capacity_kwh_per_day=choice.chargers * effective_kw * 24 * max_util,
        capex_lines=capex_lines,
        selling_price=a.get(
            fin["selling_price_inr_per_kwh"][ctype], f"finance.selling_price.{ctype}"
        ),
        # Time-of-day prices weighted by when this site draws power (simulated occupancy).
        energy_price=load_weighted_price(hourly_prices(tariff, a), sim.hourly_utilisation),
        charger_efficiency=a.get(fin["charger_efficiency"], "finance.charger_efficiency"),
        demand_charge_per_kva_month=a.get(
            tariff["demand_charge_inr_per_kva_month"], f"tariffs.{tariff['connection_type']}.demand"
        ),
        sanctioned_kva=b.sanctioned_kw / float(fin["power_factor"]),
        maintenance_pct_capex=o("maintenance_pct_of_capex_per_year"),
        insurance_pct_capex=o("insurance_pct_of_capex_per_year"),
        rent_share=o("rent_revenue_share_pct"),
        roaming_share=o("network_roaming_fee_pct"),
        payment_share=o("payment_processing_pct"),
        staff_per_year=o("staff_inr_per_site_per_year"),
        ramp=[float(r) for r in fin["utilisation_ramp"]],
        growth=a.get(fin["demand_growth_per_year"], "finance.demand_growth_per_year"),
        discount_rate=a.get(fin["discount_rate"], "finance.discount_rate"),
        horizon=int(fin["horizon_years"]),
    )
    mc_cfg = fin["monte_carlo"]
    dists = {
        k: Triangular(float(d["low"]), float(d["mode"]), float(d["high"]))
        for k, d in mc_cfg["inputs"].items()
    }
    base = cash_flows(inputs)
    named = cases(inputs, dists, fin["cases"])
    finance = {
        "method": METHOD_FINANCE,
        "subsidy": subsidy,
        "horizon_years": inputs.horizon,
        "discount_rate": inputs.discount_rate,
        "selling_price_inr_per_kwh": inputs.selling_price,
        "grid_energy_inr_per_kwh": inputs.energy_price,
        "connection": b.connection,
        "capex_lines": {k: round(v) for k, v in capex_lines.items()},
        "capex_total": round(sum(capex_lines.values())),
        "capacity_kwh_per_day": round(inputs.capacity_kwh_per_day, 1),
        "base": _flows(base),
        "cases": {name: _flows(cf) for name, cf in named.items()},
        "monte_carlo": {
            "draws": int(mc_cfg["draws"]),
            "inputs": {
                k: {**asdict(d), "label": mc_cfg["inputs"][k]["label"]} for k, d in dists.items()
            },
            **monte_carlo(inputs, dists, int(mc_cfg["draws"]), int(mc_cfg["seed"])),
        },
        "tornado": [
            {**r, "label": mc_cfg["inputs"][str(r["input"])]["label"]}
            for r in tornado(inputs, dists)
        ],
    }
    return sizing, finance


def _flows(cf: Any) -> dict[str, Any]:
    return {
        "years": cf.years,
        "energy_kwh": [round(v) for v in cf.energy_kwh],
        "revenue": [round(v) for v in cf.revenue],
        "opex": [round(v) for v in cf.opex],
        "ebitda": [round(v) for v in cf.ebitda],
        "net": [round(v) for v in cf.net],
        "capex": round(cf.capex),
        "npv": round(cf.npv),
        "irr": None if cf.irr is None else round(cf.irr, 4),
        "payback_years": None if cf.payback_years is None else round(cf.payback_years, 2),
    }


ECONOMICS_INPUTS = (
    ("sizing", "sizing.yaml"),
    ("finance.yaml",),
    ("tariffs", "maharashtra.yaml"),
    ("costs", "unit_costs.yaml"),
    ("demand", "segments.yaml"),
)


def inputs_fingerprint() -> str:
    """Hash of the configs sizing and finance depend on (files or admin overrides)."""
    import hashlib
    import json

    blob = json.dumps(
        [load_config(*parts) for parts in ECONOMICS_INPUTS], sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


async def site_economics(
    session: AsyncSession, optimisation_id: uuid.UUID, site_id: uuid.UUID, subsidy: bool
) -> SiteConfig:
    """Sizing and finance for one site of an optimisation (cached)."""
    cached = (
        await session.execute(
            select(SiteConfig).where(
                SiteConfig.optimisation_id == optimisation_id,
                SiteConfig.site_id == site_id,
                SiteConfig.subsidy == subsidy,
            )
        )
    ).scalar_one_or_none()
    fingerprint = inputs_fingerprint()
    if cached is not None:
        if cached.finance.get("inputs_fingerprint") == fingerprint:
            return cached
        # Tariffs, costs or sizing parameters changed since this was computed: redo it
        # rather than serve numbers built on old assumptions.
        await session.delete(cached)
        await session.flush()
    row = (
        await session.execute(
            select(OptimisationSite, Optimisation, CandidateSite.host_type)
            .join(Optimisation, Optimisation.id == OptimisationSite.optimisation_id)
            .join(CandidateSite, CandidateSite.id == OptimisationSite.site_id)
            .where(
                OptimisationSite.optimisation_id == optimisation_id,
                OptimisationSite.site_id == site_id,
            )
        )
    ).first()
    if row is None:
        raise EconomicsError("This site is not in that plan")
    plan_site, opt, host_type = row
    run = await session.get(PlanningRun, opt.run_id)
    assert run is not None
    year = int(run.inputs["scenario"]["target_year"])
    demand_scenario = str(opt.params["demand_scenario"])
    charger_class = plan_site.bundle.split("x")[0]

    started = time.perf_counter()
    a = Assumptions()
    usage = _usage(a)
    shares = await _segment_kwh_shares(
        session,
        site_id,
        year,
        demand_scenario,
        charger_type(charger_class),
        usage,
        load_config("sizing", "sizing.yaml"),
    )
    sizing, finance = await asyncio.get_running_loop().run_in_executor(
        _THREAD, _compute, plan_site.served_kwh, charger_class, host_type, shares, usage, a, subsidy
    )
    sizing["plan_bundle"] = {"bundle": plan_site.bundle, "charge_points": plan_site.charge_points}
    sizing["compute_s"] = round(time.perf_counter() - started, 2)
    config = SiteConfig(
        optimisation_id=optimisation_id,
        site_id=site_id,
        subsidy=subsidy,
        sizing=sizing,
        finance={**finance, "inputs_fingerprint": fingerprint},
        placeholders=a.placeholders_in_use,
    )
    session.add(config)
    snapshot_ids = run.snapshot_ids
    confidence = "NONE" if run.inputs.get("demand_synthetic") else "LOW"
    session.add_all(
        [
            Evidence(
                subject_type="candidate_site",
                subject_id=str(site_id),
                metric="sizing:chargers",
                value_num=sizing["chargers"],
                unit="chargers",
                method=METHOD_SIZING,
                snapshot_ids=snapshot_ids,
                confidence=confidence,
                run_id=run.id,
            ),
            Evidence(
                subject_type="candidate_site",
                subject_id=str(site_id),
                metric="finance:npv_p50",
                value_num=finance["monte_carlo"]["npv"]["p50"],
                unit="INR",
                method=METHOD_FINANCE,
                snapshot_ids=snapshot_ids,
                confidence=confidence,
                run_id=run.id,
            ),
        ]
    )
    await session.commit()
    return config


async def economics_step(
    session: AsyncSession, run: PlanningRun, base_id: uuid.UUID, progress: Any
) -> dict[str, Any]:
    """Run step: size and value every site of the base plan; portfolio totals go on the
    plan's KPIs. Sites whose catchment has no demand for the charger type are skipped
    with the reason."""
    await progress(session, run, "economics", "started")
    site_ids = list(
        (
            await session.execute(
                select(OptimisationSite.site_id).where(OptimisationSite.optimisation_id == base_id)
            )
        ).scalars()
    )
    configs, skipped = [], {}
    for sid in site_ids:
        try:
            configs.append(await site_economics(session, base_id, sid, subsidy=False))
        except EconomicsError as exc:
            skipped[str(sid)] = str(exc)
    base = await session.get(Optimisation, base_id)
    assert base is not None
    summary = {
        "sites": len(configs),
        "skipped": skipped,
        "chargers_sized": sum(c.sizing["chargers"] for c in configs),
        "capex_inr": sum(c.finance["capex_total"] for c in configs),
        "npv_base_case_inr": sum(c.finance["base"]["npv"] for c in configs),
        # A sum of per-site P50s is not the portfolio P50; shown as a sum and labelled so.
        "npv_p50_sum_inr": sum(c.finance["monte_carlo"]["npv"]["p50"] or 0 for c in configs),
        "sites_npv_positive_p50": sum(
            1 for c in configs if (c.finance["monte_carlo"]["npv"]["p50"] or 0) > 0
        ),
        "subsidy": False,
    }
    base.kpis = {**base.kpis, "economics": summary}
    await progress(session, run, "economics", "completed", sites=len(configs))
    return summary
