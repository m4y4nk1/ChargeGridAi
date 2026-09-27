"""Headless entry point for every ingestion job (Section 13).

python -m app.ingestion.cli sync-sources
python -m app.ingestion.cli osm <pbf>              # osm2pgsql import + snapshot
python -m app.ingestion.cli places <pbf>           # gazetteer only (osm does it too)
python -m app.ingestion.cli discom <csv> --discom MSEDCL   # grid asset ratings/loadings
python -m app.ingestion.cli ocpi <operator>        # OCPI 2.2.1 locations + CDRs/sessions
python -m app.ingestion.cli calibrate              # LightGBM residual calibration -> MLflow
python -m app.ingestion.cli policy                 # policy PDFs -> policy_chunk (RAG)
python -m app.ingestion.cli osm-chargers
python -m app.ingestion.cli ocm                    # needs OCM_API_KEY
python -m app.ingestion.cli gov-chargers <csv> [--source bee_evyatra]
python -m app.ingestion.cli fuse
python -m app.ingestion.cli vahan <csv>
python -m app.ingestion.cli vahan --synthetic
python -m app.ingestion.cli population [--grid 100m|1km] [--year 2025] [--raster <tif>]
python -m app.ingestion.cli demand [--multiplier 1.3] [--bootstrap 200]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import urllib.request
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.regions import default_region_id
from app.db.session import async_session_factory
from app.ingestion import pipelines
from app.ingestion.provenance import (
    previous_row_count,
    quality_report,
    record_snapshot,
    sync_data_sources,
)
from app.ingestion.synthetic import generate_synthetic_vahan_csv
from app.providers.osm.import_pbf import import_pbf
from app.providers.rasters.worldpop import crop_to_bbox, worldpop_ind_filename, worldpop_ind_url
from app.services.demand_run import run_demand

DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).resolve().parents[3] / "data"
OSM_TABLES = ("road_segment", "poi", "grid_asset", "admin_boundary", "landcover")


async def _osm(pbf: Path) -> pipelines.JobSummary:
    retrieved_at = datetime.fromtimestamp(pbf.stat().st_mtime, UTC)
    import_pbf(pbf)
    async with async_session_factory() as session:
        await sync_data_sources(session)
        pruned = await pipelines.prune_admin_boundaries(session)
        places = await pipelines.ingest_places(session, pbf)
        counts = {
            t: (await session.execute(text(f"select count(*) from {t}"))).scalar_one()
            for t in OSM_TABLES
        }
        report = quality_report(
            [],
            (),
            previous_row_count=await previous_row_count(session, "osm", "feature"),
            table_rows=counts,
            input_file=pbf.name,
            admin_boundaries_outside_regions_dropped=pruned,
            gazetteer_places=places,
        )
        report["row_count"] = sum(counts.values())
        snapshot = await record_snapshot(
            session,
            source_id="osm",
            retrieved_at=retrieved_at,
            raw=pbf.read_bytes(),
            raw_filename=pbf.name,
            row_count=sum(counts.values()),
            granularity="feature",
            report=report,
        )
        await session.commit()
        return pipelines.JobSummary("osm", str(snapshot.id), sum(counts.values()), report)


def _prepare_population_raster(year: int, grid: str, region_id: str) -> Path:
    """Download the national WorldPop raster, crop it to the region, and delete the original."""
    raw_dir = DATA_DIR / "raw" / "worldpop"
    cropped = raw_dir / f"{region_id}_{worldpop_ind_filename(year, grid).removeprefix('ind_')}"
    if cropped.exists():
        return cropped
    national = raw_dir / worldpop_ind_filename(year, grid)
    if not national.exists():
        raw_dir.mkdir(parents=True, exist_ok=True)
        url = worldpop_ind_url(year, grid)
        print(f"Downloading {url} (the server does not support range reads or resuming)")
        partial = national.with_suffix(".part")
        urllib.request.urlretrieve(url, partial)
        partial.rename(national)
    crop_to_bbox(national, cropped, pipelines.region_bbox(region_id))
    national.unlink()
    return cropped


async def _solar_coverage(session: Any, points: int) -> dict[str, object]:
    """docs/verification.md item 3: how many of the latest run's top-ranked candidates
    Google Solar covers. Only derived counts are printed; no Solar content is kept."""
    from app.core.settings import get_settings
    from app.db.repositories.api_usage import cost_guard_for
    from app.providers.google.solar import GoogleSolar

    key = get_settings().google_maps_server_key
    if not key:
        raise SystemExit("Set GOOGLE_MAPS_SERVER_KEY (and the cost cap and INR/USD rate) first")
    rows = (
        await session.execute(
            text(
                "select ST_Y(s.geom), ST_X(s.geom) from run_site rs "
                "join candidate_site s on s.id = rs.site_id "
                "where rs.run_id = (select id from planning_run where status = 'succeeded' "
                "order by created_at desc limit 1) and rs.rank is not null "
                "order by rs.rank limit :n"
            ),
            {"n": points},
        )
    ).all()
    solar = GoogleSolar(key, cost_guard_for(session))
    found = 0
    qualities: dict[str, int] = {}
    for lat, lng in rows:
        insight = await solar.building_insights(float(lat), float(lng))
        if insight is not None:
            found += 1
            qualities[insight.imagery_quality] = qualities.get(insight.imagery_quality, 0) + 1
    return {
        "points": len(rows),
        "covered": found,
        "not_found_share": round(1 - found / len(rows), 3) if rows else None,
        "imagery_quality": qualities,
        "checked_at": datetime.now(UTC).isoformat(),
    }


async def _run(args: argparse.Namespace) -> object:
    if args.job == "osm":
        return await _osm(Path(args.path))
    if args.job == "places":
        async with async_session_factory() as session:
            return {"places": await pipelines.ingest_places(session, Path(args.path))}

    async with async_session_factory() as session:
        await sync_data_sources(session)
        match args.job:
            case "sync-sources":
                return "data_source registry synced"
            case "discom":
                from app.ingestion.discom import ingest_discom

                path = Path(args.path)
                return await ingest_discom(session, path.read_bytes(), path.name, args.discom)
            case "ocpi":
                return await pipelines.ingest_ocpi(session, args.operator)
            case "calibrate":
                from app.services.calibration import calibrate

                return await calibrate(session)
            case "policy":
                from app.agents.rag.policy import ingest as ingest_policy

                return await ingest_policy(session)
            case "osm-chargers":
                return await pipelines.ingest_osm_chargers(session)
            case "ocm":
                return await pipelines.ingest_ocm(session, args.region)
            case "gov-chargers":
                return await pipelines.ingest_government_chargers(
                    session, Path(args.path), args.source
                )
            case "fuse":
                return await pipelines.run_charger_fusion(session)
            case "vahan":
                if args.synthetic:
                    path = DATA_DIR / "synthetic" / "vahan_pmr_synthetic.csv"
                    generate_synthetic_vahan_csv(path)
                    return await pipelines.ingest_vahan(session, path, "synthetic_vahan")
                if not args.path:
                    raise SystemExit("vahan needs a CSV path or --synthetic")
                return await pipelines.ingest_vahan(session, Path(args.path), "vahan")
            case "demand":
                return await run_demand(
                    session,
                    multiplier=args.multiplier,
                    n_bootstrap=args.bootstrap,
                    region_id=args.region or default_region_id(),
                )
            case "solar":
                return await pipelines.ingest_solar(session, args.year, args.region)
            case "solar-coverage":
                return await _solar_coverage(session, args.points)
            case "population":
                region = args.region or default_region_id()
                raster = (
                    Path(args.raster)
                    if args.raster
                    else _prepare_population_raster(args.year, args.grid, region)
                )
                return await pipelines.ingest_population(
                    session, raster, args.year, args.grid, region_id=region
                )
    raise SystemExit(f"unknown job {args.job}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.ingestion.cli")
    sub = parser.add_subparsers(dest="job", required=True)
    sub.add_parser("sync-sources")
    sub.add_parser("osm").add_argument("path")
    sub.add_parser("places").add_argument("path")
    sub.add_parser("policy")
    sub.add_parser("ocpi").add_argument("operator", help="id in config/ocpi_operators.yaml")
    sub.add_parser("calibrate")
    discom = sub.add_parser("discom")
    discom.add_argument("path")
    discom.add_argument("--discom", required=True, help="e.g. MSEDCL")
    sub.add_parser("osm-chargers")
    ocm = sub.add_parser("ocm")
    ocm.add_argument("--region", help="config/regions.yaml id; default: the default region")
    gov = sub.add_parser("gov-chargers")
    gov.add_argument("path")
    gov.add_argument("--source", default="bee_evyatra")
    sub.add_parser("fuse")
    vahan = sub.add_parser("vahan")
    vahan.add_argument("path", nargs="?")
    vahan.add_argument("--synthetic", action="store_true")
    pop = sub.add_parser("population")
    pop.add_argument("--raster")
    pop.add_argument("--year", type=int, default=2025)
    pop.add_argument("--grid", choices=["100m", "1km"], default="100m")
    pop.add_argument("--region")
    solar = sub.add_parser("solar")
    solar.add_argument("--year", type=int, default=2024)
    solar.add_argument("--region")
    coverage = sub.add_parser("solar-coverage")
    coverage.add_argument("--points", type=int, default=20)
    demand = sub.add_parser("demand")
    demand.add_argument("--multiplier", type=float, default=1.0)
    demand.add_argument("--bootstrap", type=int, default=200)
    demand.add_argument("--region")

    result = asyncio.run(_run(parser.parse_args()))
    print(
        json.dumps(
            asdict(result) if is_dataclass(result) and not isinstance(result, type) else result,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
