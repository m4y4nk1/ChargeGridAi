import uuid
from datetime import datetime
from decimal import Decimal

import pytest

from app.core.charger_classes import power_band
from app.core.cost_guard import BudgetExceeded, CostGuard, PricingTable, SkuPrice


class MemoryLedger:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, int, Decimal, uuid.UUID | None]] = []

    async def spent_inr(self, provider: str, since: datetime) -> Decimal:
        return sum((r[3] for r in self.rows if r[0] == provider), Decimal(0))

    async def spent_inr_for_run(self, run_id: uuid.UUID) -> Decimal:
        return sum((r[3] for r in self.rows if r[4] == run_id), Decimal(0))

    async def units_used(self, provider: str, sku: str, since: datetime) -> int:
        return sum(r[2] for r in self.rows if r[0] == provider and r[1] == sku)

    async def record(
        self, provider: str, sku: str, units: int, cost_inr: Decimal, run_id: uuid.UUID | None
    ) -> None:
        self.rows.append((provider, sku, units, cost_inr, run_id))


PRICING = PricingTable(
    inr_per_usd=Decimal("80"),
    skus={("google_geocoding", "geocode"): SkuPrice(Decimal("1.50"), free_units_per_month=2)},
)


def guard(
    cap: int | None, run_cap: int | None = None, run_id: uuid.UUID | None = None
) -> CostGuard:
    return CostGuard(MemoryLedger(), PRICING, {"google_geocoding": cap}, run_cap, run_id)


async def test_fails_closed_without_a_cap() -> None:
    with pytest.raises(BudgetExceeded, match="No monthly cap"):
        await guard(cap=None).charge("google_geocoding", "geocode")


async def test_fails_closed_without_a_price() -> None:
    g = CostGuard(MemoryLedger(), PricingTable(None, {}), {"google_places": 1000}, None)
    with pytest.raises(BudgetExceeded, match="No price"):
        await g.charge("google_places", "search_nearby")


async def test_free_tier_then_billing() -> None:
    g = guard(cap=100)
    assert await g.charge("google_geocoding", "geocode", 2) == Decimal("0.00")
    # 1000 billable calls at $1.50/1000 x ₹80 = ₹120 > ₹100 cap
    with pytest.raises(BudgetExceeded, match="monthly cap"):
        await g.charge("google_geocoding", "geocode", 1000)
    assert await g.charge("google_geocoding", "geocode", 500) == Decimal("60.00")


async def test_per_run_cap() -> None:
    g = guard(cap=10_000, run_cap=50, run_id=uuid.uuid4())
    await g.charge("google_geocoding", "geocode", 2)
    with pytest.raises(BudgetExceeded, match="Per-run cap"):
        await g.charge("google_geocoding", "geocode", 500)


def test_repo_pricing_config_enables_only_fully_priced_skus() -> None:
    from app.core.settings import get_settings

    table = PricingTable.load(get_settings().config_dir / "costs" / "api_pricing.yaml")
    # Sourced FX rate (Fed H.10) is set, so priced SKUs run under the monthly cap;
    # Places Text Search has no captured price and stays disabled.
    assert table.inr_per_usd is not None and 50 < table.inr_per_usd < 150
    assert ("google_solar", "building_insights") in table.skus
    assert table.skus[("google_places", "search_nearby")].usd_per_1000 == 32
    assert ("google_places", "search_text") not in table.skus


@pytest.mark.parametrize(
    ("current", "kw", "band"),
    [
        ("AC", 7.4, "AC"),
        ("AC", 43, "AC"),
        (None, 22, "AC"),
        ("DC", 30, "DC <60 kW"),
        ("DC", 60, "DC 60–149 kW"),
        ("DC", 240, "DC 150–349 kW"),
        ("DC", 350, "DC ≥350 kW"),
        ("DC", None, "Unknown"),
        (None, None, "Unknown"),
    ],
)
def test_power_bands(current: str | None, kw: float | None, band: str) -> None:
    assert power_band(current, kw) == band
