"""Grid tariffs (config/tariffs/maharashtra.yaml) resolved through Assumptions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.assumptions import Assumptions


def hourly_prices(tariff: dict[str, Any], a: Assumptions) -> list[float]:
    """INR/kWh for each hour: the energy charge plus any time-of-day adder."""
    kind = tariff["connection_type"]
    base = a.get(tariff["energy_charge_inr_per_kwh"], f"tariffs.{kind}.energy")
    prices = [base] * 24
    for i, slab in enumerate(tariff.get("tod_slabs") or []):
        adder = a.get(slab["adder_inr_per_kwh"], f"tariffs.{kind}.tod.{i}")
        for h in range(int(slab["start"]), int(slab["end"])):
            prices[h] = base + adder
    return prices


def load_weighted_price(prices: Sequence[float], load: Sequence[float]) -> float:
    """Average INR/kWh a site with this hourly load profile pays."""
    total = sum(load)
    if total <= 0:
        return sum(prices) / len(prices)
    return sum(p * w for p, w in zip(prices, load, strict=True)) / total
