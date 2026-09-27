"""Region onboarding (Phase 12): take a region registered in config/regions.yaml
(`status: planned`) to planning-ready, one idempotent step at a time.

    python -m app.ingestion.onboard <region> --pbf /data/osm/<source>.osm.pbf [--dry-run]
    make onboard-region REGION=<region> PBF=/data/osm/<source>.osm.pbf

Steps: check the registry entry -> cut the region from the source extract -> merge it
with the other onboarded regions' extracts -> import OSM (roads, POIs, boundaries,
gazetteer) -> population -> registrations check -> demand -> chargers (OCM + fusion)
-> solar -> readiness report. The routing graph and the final `status: onboarded`
flip are left to a person (see docs/onboarding-regions.md): both need a restart or a
review, not a script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from app.core.regions import get_region
from app.core.settings import get_settings

DATA = Path("/data") if Path("/data").is_dir() else Path(__file__).resolve().parents[3] / "data"


def _run(cmd: list[str], dry: bool) -> None:
    print("  $", " ".join(cmd))
    if not dry:
        subprocess.run(cmd, check=True)


async def onboard(region_id: str, pbf: Path, dry: bool, skip: set[str]) -> dict[str, Any]:
    from sqlalchemy import text

    from app.db.session import async_session_factory
    from app.ingestion import pipelines
    from app.ingestion.cli import _osm, _prepare_population_raster
    from app.ingestion.provenance import sync_data_sources
    from app.services.demand_run import run_demand
    from app.services.readiness import region_readiness

    region = get_region(region_id)
    b = region.bbox
    report: dict[str, Any] = {"region": region.id, "steps": []}

    def step(name: str) -> bool:
        run = name not in skip
        print(f"[{'run ' if run and not dry else 'plan' if run else 'skip'}] {name}")
        report["steps"].append({"step": name, "ran": run and not dry})
        return run

    # 1. Registry entry
    if step("check registry"):
        problems = []
        if not region.rtos:
            problems.append(
                "no RTO codes: demand needs the region's RTO list and catchments "
                "(config/regions.yaml, config/demand/allocation.yaml)"
            )
        if not pbf.exists():
            problems.append(f"source extract {pbf} not found (download from Geofabrik)")
        for p in problems:
            print("  !", p)
        report["registry_problems"] = problems
        if problems and not dry:
            raise SystemExit("Fix the registry entry first (see above)")

    # 2-3. Extract and merge
    extract = DATA / "osm" / f"{region.id}.osm.pbf"
    merged = DATA / "osm" / "onboarded_merged.osm.pbf"
    if step("extract region"):
        _run(
            [
                "osmium",
                "extract",
                "-b",
                f"{b.min_lng},{b.min_lat},{b.max_lng},{b.max_lat}",
                "--strategy",
                "simple",
                str(pbf),
                "-o",
                str(extract),
                "--overwrite",
            ],
            dry,
        )
        admin = DATA / "osm" / f"{region.id}_admin.osm.pbf"
        _run(
            [
                "osmium",
                "tags-filter",
                str(pbf),
                "r/admin_level=4,5,6",
                "-o",
                str(admin),
                "--overwrite",
            ],
            dry,
        )
    if step("merge with onboarded regions"):
        sources = sorted(
            str(p)
            for p in (DATA / "osm").glob("*.osm.pbf")
            if p.name not in (merged.name,) and not p.name.startswith("western-")
        )
        _run(["osmium", "merge", *sources, "-o", str(merged), "--overwrite"], dry)

    # 4. OSM import (replaces the OSM tables with the merged extract)
    if step("import OSM") and not dry:
        report["osm"] = str((await _osm(merged)).rows)

    async with async_session_factory() as session:
        await sync_data_sources(session)
        # 5. Population
        if step("population") and not dry:
            raster = _prepare_population_raster(2025, "1km", region.id)
            report["population"] = (
                await pipelines.ingest_population(
                    session, raster, 2025, grid="1km", region_id=region.id
                )
            ).rows
        # 6. Registrations
        if step("registrations check"):
            n = int(
                (
                    await session.execute(
                        text(
                            "select count(*) from vehicle_registration_agg where rto_code = any(:r)"
                        ),
                        {"r": list(region.rtos)},
                    )
                ).scalar()
                or 0
            )
            report["registration_rows"] = n
            if not n:
                print(
                    "  ! no registrations for this region's RTOs: import a VAHAN export "
                    "(python -m app.ingestion.cli vahan <csv>) before demand"
                )
        # 7. Demand
        if step("demand") and not dry and report.get("registration_rows"):
            report["demand"] = (await run_demand(session, region_id=region.id)).scenarios
        # 8. Chargers
        if step("chargers") and not dry and get_settings().ocm_api_key:
            await pipelines.ingest_ocm(session, region.id)
            await pipelines.run_charger_fusion(session)
        # 9. Solar
        if step("solar") and not dry:
            await pipelines.ingest_solar(session, 2024, region.id)
        # 10. Readiness
        if step("readiness"):
            report["readiness"] = await region_readiness(session, region)

    print("\nNext, by hand (docs/onboarding-regions.md):")
    print(
        f"  - routing: copy {merged.name} to infra/docker/valhalla/custom_files/, remove the "
        "old extract there, `docker compose restart valhalla` (rebuilds tiles)"
    )
    print(
        f"  - review the readiness report, then set `status: onboarded` for {region.id} in "
        "config/regions.yaml"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.ingestion.onboard")
    parser.add_argument("region")
    parser.add_argument("--pbf", required=True, type=Path, help="source extract covering it")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip", default="", help="comma-separated step names to skip")
    args = parser.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    result = asyncio.run(onboard(args.region, args.pbf, args.dry_run, skip))
    print(json.dumps(result, indent=1, default=str))


if __name__ == "__main__":
    main()
