"""Grid Impact switches from the proximity proxy to DISCOM-backed assessment when asset
ratings exist (Phase 10 acceptance). Runs against the local database inside a
transaction that is rolled back; skipped when no database or optimised run exists.
The DISCOM assets here are test fixtures, never real data."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.settings import get_settings
from app.db.models.grid import GridAssetRated
from app.services.grid_impact import grid_impact


@pytest.fixture
async def session():  # type: ignore[no-untyped-def]
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:
        await engine.dispose()
        pytest.skip("no database")
    trans = await conn.begin()
    s = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield s
    finally:
        await s.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_proxy_then_discom_backed(session: AsyncSession) -> None:
    run_id = await session.scalar(
        text(
            "select id from planning_run where status = 'succeeded' and "
            "funnel->'optimisation'->>'base_id' is not null order by created_at desc limit 1"
        )
    )
    if run_id is None:
        pytest.skip("no optimised run")
    await session.execute(text("delete from grid_asset_rated"))
    proxy = await grid_impact(session, run_id)
    assert proxy["mode"] == "proxy" and proxy["confidence"] in ("LOW", "NONE")
    assert proxy["assets"] == [] and proxy["utilisation"] is None
    assert proxy["badge"].startswith("Proximity-only")

    # Fixture: a substation at every plan site (HT sites draw from substations), one
    # nearly full so the plan overloads it.
    today = datetime.now(UTC).date()
    for i, site in enumerate(proxy["sites"]):
        lat, lng = (
            await session.execute(
                text("select ST_Y(geom), ST_X(geom) from candidate_site where id = :id"),
                {"id": uuid.UUID(site["site_id"])},
            )
        ).one()
        session.add(
            GridAssetRated(
                id=f"TEST:SS-{i}",
                discom="TEST",
                asset_type="substation",
                name=f"Fixture substation {i}",
                rated_kva=20000,
                loading_limit=0.8,
                base_peak_kva=15990 if i == 0 else 8000,
                measured_on=today - timedelta(days=30),
                license_class="RESTRICTED_COMMERCIAL",
                extra={},
                geom=f"SRID=4326;POINT({lng + 0.001} {lat})",
            )
        )
    await session.flush()
    backed = await grid_impact(session, run_id)
    assert backed["mode"] == "discom" and backed["confidence"] == "MEDIUM"
    assert backed["badge"] == "DISCOM asset ratings and loadings"
    assert len(backed["utilisation"]["base"]) == len(backed["assets"]) > 0
    first = next(a for a in backed["assets"] if a["asset_id"] == "TEST:SS-0")
    assert first["overloaded"] and first["upgrade_cost_inr"] is None
    assert backed["upgrades"]["substations_needing_study"] >= 1
    # Same plan, same load curve: only the grid side changed.
    assert backed["load_curve"] == proxy["load_curve"]
