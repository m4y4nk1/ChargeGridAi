import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cost_guard import CostGuard, PricingTable
from app.core.settings import get_settings
from app.db.models.provenance import ApiUsage


class SqlUsageLedger:
    """CostGuard ledger backed by `api_usage`. Commits each reservation immediately."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def spent_inr(self, provider: str, since: datetime) -> Decimal:
        total = await self._session.scalar(
            select(func.coalesce(func.sum(ApiUsage.cost_inr), 0)).where(
                ApiUsage.provider == provider, ApiUsage.ts >= since
            )
        )
        return Decimal(total or 0)

    async def spent_inr_for_run(self, run_id: uuid.UUID) -> Decimal:
        total = await self._session.scalar(
            select(func.coalesce(func.sum(ApiUsage.cost_inr), 0)).where(ApiUsage.run_id == run_id)
        )
        return Decimal(total or 0)

    async def units_used(self, provider: str, sku: str, since: datetime) -> int:
        total = await self._session.scalar(
            select(func.coalesce(func.sum(ApiUsage.units), 0)).where(
                ApiUsage.provider == provider, ApiUsage.sku == sku, ApiUsage.ts >= since
            )
        )
        return int(total or 0)

    async def record(
        self, provider: str, sku: str, units: int, cost_inr: Decimal, run_id: uuid.UUID | None
    ) -> None:
        self._session.add(
            ApiUsage(
                provider=provider,
                sku=sku,
                units=units,
                cost_inr=cost_inr,
                run_id=run_id,
                ts=datetime.now(UTC),
            )
        )
        await self._session.commit()


def cost_guard_for(session: AsyncSession, run_id: uuid.UUID | None = None) -> CostGuard:
    settings = get_settings()
    google_cap = settings.cost_cap_monthly_inr_google
    return CostGuard(
        ledger=SqlUsageLedger(session),
        pricing=PricingTable.load(settings.config_dir / "costs" / "api_pricing.yaml"),
        monthly_caps_inr={
            "google_places": google_cap,
            "google_geocoding": google_cap,
            "google_solar": google_cap,
        },
        per_run_cap_inr=settings.cost_cap_per_run_inr,
        run_id=run_id,
    )
