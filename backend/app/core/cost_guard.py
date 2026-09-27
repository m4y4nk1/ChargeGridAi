"""Paid-API cost guard (Section 2 principle 7; Section 7.2 adapter rules).

Every paid call reserves its cost *before* the request is made. The guard
fails closed: a provider with no configured monthly cap, or a SKU with no
configured price, is refused outright — spending money must be an explicit
configuration decision, never a default. Callers catch BudgetExceeded and
degrade to open data.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import yaml


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class SkuPrice:
    usd_per_1000: Decimal
    free_units_per_month: int


@dataclass(frozen=True)
class PricingTable:
    inr_per_usd: Decimal | None
    skus: dict[tuple[str, str], SkuPrice]

    @classmethod
    def load(cls, path: Path) -> PricingTable:
        data = yaml.safe_load(path.read_text())
        fx = data.get("inr_per_usd", {}).get("value")
        skus: dict[tuple[str, str], SkuPrice] = {}
        for provider, entries in (data.get("providers") or {}).items():
            for sku, entry in entries.items():
                price = entry.get("usd_per_1000")
                if isinstance(price, int | float):
                    skus[(provider, sku)] = SkuPrice(
                        usd_per_1000=Decimal(str(price)),
                        free_units_per_month=int(entry.get("free_units_per_month") or 0),
                    )
        return cls(
            inr_per_usd=Decimal(str(fx)) if isinstance(fx, int | float) else None,
            skus=skus,
        )


class UsageLedger(Protocol):
    async def spent_inr(self, provider: str, since: datetime) -> Decimal: ...
    async def spent_inr_for_run(self, run_id: uuid.UUID) -> Decimal: ...
    async def units_used(self, provider: str, sku: str, since: datetime) -> int: ...
    async def record(
        self, provider: str, sku: str, units: int, cost_inr: Decimal, run_id: uuid.UUID | None
    ) -> None: ...


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class CostGuard:
    def __init__(
        self,
        ledger: UsageLedger,
        pricing: PricingTable,
        monthly_caps_inr: dict[str, int | None],
        per_run_cap_inr: int | None,
        run_id: uuid.UUID | None = None,
    ) -> None:
        self._ledger = ledger
        self._pricing = pricing
        self._monthly_caps = monthly_caps_inr
        self._per_run_cap = per_run_cap_inr
        self._run_id = run_id

    async def charge(self, provider: str, sku: str, units: int = 1) -> Decimal:
        """Reserve the cost of `units` calls; raises BudgetExceeded instead of overspending."""
        cap = self._monthly_caps.get(provider)
        if cap is None:
            raise BudgetExceeded(
                f"No monthly cap configured for {provider}; paid calls are disabled"
            )
        price = self._pricing.skus.get((provider, sku))
        if price is None or self._pricing.inr_per_usd is None:
            raise BudgetExceeded(
                f"No price configured for {provider}/{sku}; paid calls are disabled"
            )

        now = datetime.now(UTC)
        month_start = _month_start(now)
        used = await self._ledger.units_used(provider, sku, month_start)
        free_remaining = max(0, price.free_units_per_month - used)
        billable = max(0, units - free_remaining)
        cost = (price.usd_per_1000 * self._pricing.inr_per_usd * billable / 1000).quantize(
            Decimal("0.01")
        )

        if await self._ledger.spent_inr(provider, month_start) + cost > cap:
            raise BudgetExceeded(f"{provider} monthly cap of ₹{cap} would be exceeded")
        if self._run_id is not None and self._per_run_cap is not None:
            if await self._ledger.spent_inr_for_run(self._run_id) + cost > self._per_run_cap:
                raise BudgetExceeded(f"Per-run cap of ₹{self._per_run_cap} would be exceeded")

        await self._ledger.record(provider, sku, units, cost, self._run_id)
        return cost
