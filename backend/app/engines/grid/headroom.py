"""Grid headroom and upgrade assessment (Section 9.11, advanced).

For an asset with a DISCOM rating and measured loading:

    capacity_kva  = rated_kva x loading_limit
    headroom_kva  = capacity_kva - base_peak_kva
    peak_with_kva = base peak + the plan sites' coincident added peak

With an hourly base profile the added load is summed hour by hour and the peak read
off the combined curve; without one, the sites' own peak is added at the configured
coincidence factor (1.0 = conservative). An overloaded distribution transformer is
costed as a replacement with the next standard size; substations are flagged for a
DISCOM study and not costed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from app.engines.grid.load_profile import HOURS


@dataclass(frozen=True)
class AssetRating:
    asset_id: str
    asset_type: str  # transformer | substation
    rated_kva: float
    loading_limit: float
    base_peak_kva: float
    measured_on: date | None
    base_hourly_kva: Sequence[float] | None = None


@dataclass(frozen=True)
class SiteLoad:
    site_id: str
    hourly_kw: Sequence[float]
    sanctioned_kw: float


@dataclass(frozen=True)
class UpgradeRules:
    transformer_sizes_kva: Sequence[float]
    transformer_inr_per_kva: float
    transformer_fixed_inr: float


@dataclass
class AssetAssessment:
    asset_id: str
    asset_type: str
    rated_kva: float
    capacity_kva: float
    base_peak_kva: float
    headroom_kva: float
    added_peak_kva: float
    peak_with_kva: float
    utilisation_base: float
    utilisation_with: float
    overloaded: bool
    upgrade: str | None
    upgrade_to_kva: float | None
    upgrade_cost_inr: float | None
    confidence: str
    site_ids: list[str] = field(default_factory=list)
    hourly_with_kva: list[float] | None = None


def confidence_for(
    measured_on: date | None, has_hourly: bool, today: date, medium_days: int, high_days: int
) -> str:
    if measured_on is None:
        return "LOW"
    age = (today - measured_on).days
    if has_hourly and age <= high_days:
        return "HIGH"
    return "MEDIUM" if age <= medium_days else "LOW"


def assess(
    asset: AssetRating,
    sites: Sequence[SiteLoad],
    power_factor: float,
    coincidence: float,
    rules: UpgradeRules,
    today: date,
    medium_days: int = 365,
    high_days: int = 90,
) -> AssetAssessment:
    if asset.rated_kva <= 0 or not 0 < asset.loading_limit <= 1:
        raise ValueError(f"asset {asset.asset_id}: bad rating or loading limit")
    if not 0 < power_factor <= 1:
        raise ValueError("power_factor must be in (0, 1]")
    added = [sum(s.hourly_kw[h] for s in sites) / power_factor for h in range(HOURS)]
    added_peak = max(added) if sites else 0.0
    hourly_with: list[float] | None = None
    if asset.base_hourly_kva is not None and len(asset.base_hourly_kva) == HOURS:
        base_peak = max(asset.base_hourly_kva)
        hourly_with = [asset.base_hourly_kva[h] + added[h] for h in range(HOURS)]
        peak_with = max(hourly_with)
    else:
        base_peak = asset.base_peak_kva
        peak_with = base_peak + coincidence * added_peak
    capacity = asset.rated_kva * asset.loading_limit
    overloaded = peak_with > capacity + 1e-9
    upgrade = upgrade_to = cost = None
    if overloaded and asset.asset_type == "transformer":
        needed = peak_with / asset.loading_limit
        larger = [s for s in sorted(rules.transformer_sizes_kva) if s >= needed]
        if larger:
            upgrade_to = float(larger[0])
            upgrade = f"replace with a {larger[0]:g} kVA transformer"
            cost = rules.transformer_fixed_inr + rules.transformer_inr_per_kva * (
                upgrade_to - asset.rated_kva
            )
        else:
            upgrade = "beyond the largest standard transformer: new transformer or HT supply"
    elif overloaded:
        upgrade = "substation augmentation: needs a DISCOM system study (not costed)"
    return AssetAssessment(
        asset_id=asset.asset_id,
        asset_type=asset.asset_type,
        rated_kva=asset.rated_kva,
        capacity_kva=capacity,
        base_peak_kva=base_peak,
        headroom_kva=capacity - base_peak,
        added_peak_kva=added_peak,
        peak_with_kva=peak_with,
        utilisation_base=base_peak / asset.rated_kva,
        utilisation_with=peak_with / asset.rated_kva,
        overloaded=overloaded,
        upgrade=upgrade,
        upgrade_to_kva=upgrade_to,
        upgrade_cost_inr=None if cost is None else math.ceil(cost),
        confidence=confidence_for(
            asset.measured_on, hourly_with is not None, today, medium_days, high_days
        ),
        site_ids=[s.site_id for s in sites],
        hourly_with_kva=hourly_with,
    )
