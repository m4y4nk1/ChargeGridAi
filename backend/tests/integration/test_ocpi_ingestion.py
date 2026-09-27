"""OCPI ingestion end to end against a fake operator: locations are staged and fused,
CDRs become observed sessions and daily utilisation. Local database, rolled back."""

import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.ingestion import pipelines
from app.ingestion.provenance import sync_data_sources
from app.providers.ocpi import client as ocpi
from tests.integration.test_grid_impact_discom import session  # noqa: F401
from tests.unit.test_ocpi_client import BASE, LOCATIONS, _env


def _operator() -> httpx.MockTransport:
    cdrs = [
        {
            "id": f"C{d}",
            "start_date_time": f"2026-08-{d:02d}T10:00:00Z",
            "end_date_time": f"2026-08-{d:02d}T10:45:00Z",
            "total_energy": 40.0 + d,
            "cdr_location": {"id": "LOC1", "evse_uid": "E1"},
        }
        for d in range(1, 31)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{BASE}/versions":
            return httpx.Response(200, json=_env([{"version": "2.2.1", "url": f"{BASE}/2.2.1"}]))
        if url == f"{BASE}/2.2.1":
            return httpx.Response(
                200,
                json=_env(
                    {
                        "version": "2.2.1",
                        "endpoints": [
                            {
                                "identifier": "locations",
                                "role": "SENDER",
                                "url": f"{BASE}/2.2.1/locations",
                            },
                            {"identifier": "cdrs", "role": "SENDER", "url": f"{BASE}/2.2.1/cdrs"},
                        ],
                    }
                ),
            )
        if "/locations" in url:
            return httpx.Response(200, json=_env(LOCATIONS))
        if "/cdrs" in url:
            return httpx.Response(200, json=_env(cdrs))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_ocpi_feed_to_daily_utilisation(
    session: AsyncSession,  # noqa: F811
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config = tmp_path / "config"
    shutil.copytree(get_settings().config_dir, config)
    (config / "ocpi_operators.yaml").write_text(
        f"operators:\n  testcpo:\n    name: Test CPO\n    versions_url: {BASE}/versions\n"
    )
    monkeypatch.setattr(get_settings(), "config_dir", config)
    monkeypatch.setenv("OCPI_TOKEN_TESTCPO", "t")
    real = ocpi.OCPIClient
    monkeypatch.setattr(
        ocpi, "OCPIClient", lambda url, token: real(url, token, transport=_operator())
    )
    await sync_data_sources(session)
    summary = await pipelines.ingest_ocpi(session, "testcpo")
    assert summary.rows == 2 and summary.notes["observed_sessions"] == 30
    assert summary.notes["unmatched_locations"] == 0
    lic = await session.scalar(
        text("select license_class from data_source where id = 'operator:testcpo'")
    )
    assert lic == "RESTRICTED_COMMERCIAL"
    stored = await session.scalar(
        text("select storage_uri from dataset_snapshot where source_id = 'operator:testcpo'")
    )
    assert stored is None  # contractual raw payloads are never kept
    days, kwh = (
        await session.execute(
            text(
                "select count(*), sum(kwh) from station_utilisation_daily "
                "where source_id = 'operator:testcpo'"
            )
        )
    ).one()
    assert days == 30 and kwh == pytest.approx(sum(40.0 + d for d in range(1, 31)))
    # Re-ingesting is idempotent.
    await pipelines.ingest_ocpi(session, "testcpo")
    assert (
        await session.scalar(
            text("select count(*) from charging_session_obs where source_id = 'operator:testcpo'")
        )
        == 30
    )
