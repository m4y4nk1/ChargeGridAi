"""Area-level reads over the demand run's per-cell rows (Area Planner, Section 3 A).

An area is an admin boundary; its cells are the region's H3 cells whose
centroid lies inside it. Quantiles are summed across cells. Within one RTO
and segment that is exact (every cell carries the same quantile of the same
forecast); across RTOs and segments it assumes they move together, so the
P10-P90 band is conservatively wide. Coverage says how much of the boundary
the modelled region actually spans.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import Assumptions, load_config
from app.core.regions import default_region_id, get_region
from app.db.models.provenance import Evidence
from app.engines.supply_gap import SupplyAssumptions, additional_charge_points

_AREA_CELLS = """
    select c.h3 from region_cell rc join h3_cell c on c.h3 = rc.h3
      join admin_boundary b on ST_Within(ST_Centroid(c.geom), b.geom)
    where b.osm_id = :area_id and rc.region_id = :region_id
"""


@dataclass
class DemandRun:
    run_id: str
    evidence_id: str
    config: dict[str, Any]

    @property
    def synthetic(self) -> bool:
        return any(s.startswith("synthetic") for s in self.config["registration_sources"])


async def latest_run(session: AsyncSession, scenario: str | None = None) -> DemandRun | None:
    """Most recent demand run, or the most recent one that produced `scenario`.

    What-if runs (e.g. `basex1.3`) don't replace the standard cases, so a
    request for 'base' must resolve to the run that wrote 'base'.
    """
    query = select(Evidence).where(
        Evidence.subject_type == "demand_run", Evidence.metric == "config"
    )
    if scenario is not None:
        query = query.where(
            text("(evidence.value_text::jsonb -> 'scenarios') ? :scenario").bindparams(
                scenario=scenario
            )
        )
    row = (
        await session.execute(query.order_by(Evidence.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    if row is None:
        return None
    return DemandRun(str(row.run_id), str(row.id), json.loads(row.value_text or "{}"))


async def area_info(
    session: AsyncSession, area_id: int, region_id: str | None = None
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "select osm_id, name, level, ST_Area(geom::geography) / 1e6 as km2, "
                    "ST_Area(ST_Intersection(geom, ST_MakeEnvelope(:w, :s, :e, :n, 4326))"
                    "::geography) / 1e6 as covered_km2 "
                    "from admin_boundary where osm_id = :id"
                ),
                {
                    "id": area_id,
                    "w": get_region(region_id).bbox.min_lng,
                    "s": get_region(region_id).bbox.min_lat,
                    "e": get_region(region_id).bbox.max_lng,
                    "n": get_region(region_id).bbox.max_lat,
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return {
        "id": row["osm_id"],
        "name": row["name"],
        "level": row["level"],
        "area_km2": round(row["km2"], 1),
        "coverage_share": round(row["covered_km2"] / row["km2"], 4) if row["km2"] else 0.0,
    }


async def area_totals(
    session: AsyncSession,
    area_id: int,
    scenarios: list[str],
    region_id: str | None = None,
) -> dict[tuple[str, int], dict[str, float]]:
    rows = (
        (
            await session.execute(
                text(
                    f"with area as ({_AREA_CELLS}) "
                    "select f.scenario, f.year, "
                    "sum(f.ev_stock_p10) p10, sum(f.ev_stock_p50) p50, sum(f.ev_stock_p90) p90, "
                    "sum(f.public_kwh_p10) kwh_p10, sum(f.public_kwh_p50) kwh_p50, "
                    "sum(f.public_kwh_p90) kwh_p90, sum(f.sessions_p50) sessions, "
                    "sum(f.gap_kwh) gap, sum(f.served_kwh) served "
                    "from h3_cell_feature f join area a on a.h3 = f.h3 "
                    "where f.scenario = any(:scenarios) group by f.scenario, f.year"
                ),
                {
                    "area_id": area_id,
                    "region_id": region_id or default_region_id(),
                    "scenarios": scenarios,
                },
            )
        )
        .mappings()
        .all()
    )
    return {
        (r["scenario"], r["year"]): {k: v for k, v in r.items() if k not in ("scenario", "year")}
        for r in rows
    }


async def area_segments(
    session: AsyncSession,
    area_id: int,
    scenario: str,
    year: int,
    region_id: str | None = None,
) -> dict[str, list[float]]:
    rows = (
        await session.execute(
            text(
                f"with area as ({_AREA_CELLS}) "
                "select key, sum((value->>0)::float), sum((value->>1)::float), "
                "sum((value->>2)::float) "
                "from h3_cell_feature f join area a on a.h3 = f.h3, "
                "jsonb_each(f.ev_stock_by_segment) "
                "where f.scenario = :scenario and f.year = :year group by key"
            ),
            {
                "area_id": area_id,
                "region_id": region_id or default_region_id(),
                "scenario": scenario,
                "year": year,
            },
        )
    ).all()
    return {key: [p10, p50, p90] for key, p10, p50, p90 in rows}


async def area_segment_series(
    session: AsyncSession,
    area_id: int,
    scenarios: list[str],
    segment: str,
    region_id: str | None = None,
) -> dict[tuple[str, int], list[float]]:
    rows = (
        await session.execute(
            text(
                f"with area as ({_AREA_CELLS}) "
                "select f.scenario, f.year, "
                "sum((f.ev_stock_by_segment->:segment->>0)::float), "
                "sum((f.ev_stock_by_segment->:segment->>1)::float), "
                "sum((f.ev_stock_by_segment->:segment->>2)::float) "
                "from h3_cell_feature f join area a on a.h3 = f.h3 "
                "where f.scenario = any(:scenarios) and f.ev_stock_by_segment ? :segment "
                "group by f.scenario, f.year"
            ),
            {
                "area_id": area_id,
                "region_id": region_id or default_region_id(),
                "scenarios": scenarios,
                "segment": segment,
            },
        )
    ).all()
    return {(sc, year): [p10, p50, p90] for sc, year, p10, p50, p90 in rows}


async def area_population(
    session: AsyncSession, area_id: int, region_id: str | None = None
) -> tuple[float, int | None]:
    row = (
        await session.execute(
            text(
                f"with area as ({_AREA_CELLS}) "
                "select sum(f.population), max(f.year) from h3_cell_feature f "
                "join area a on a.h3 = f.h3 where f.scenario = 'observed' "
                "and f.year = (select max(year) from h3_cell_feature where scenario = 'observed')"
            ),
            {"area_id": area_id, "region_id": region_id or default_region_id()},
        )
    ).first()
    return (float(row[0] or 0.0), row[1]) if row else (0.0, None)


async def area_charge_points(session: AsyncSession, area_id: int) -> dict[str, int]:
    """Existing public charge points in the area, separating reported from assumed counts."""
    a = Assumptions()
    assumed_per_station = int(
        a.get(
            load_config("demand", "supply.yaml")["assumed_charge_points_when_unknown"],
            "supply.assumed_charge_points_when_unknown",
        )
    )
    row = (
        await session.execute(
            text(
                "select count(*) stations, "
                "coalesce(sum(e.cp), 0) reported, "
                "count(*) filter (where e.cp is null) without_counts "
                "from charging_station s join admin_boundary b on ST_Within(s.geom, b.geom) "
                "left join (select station_id, sum(count) cp from evse group by station_id) e "
                "on e.station_id = s.id where b.osm_id = :area_id"
            ),
            {"area_id": area_id},
        )
    ).first()
    stations, reported, without = (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)
    return {
        "stations": stations,
        "reported_charge_points": reported,
        "stations_without_counts": without,
        "charge_points_incl_assumed": reported + without * assumed_per_station,
    }


def supply_assumptions() -> tuple[SupplyAssumptions, list[str]]:
    a = Assumptions()
    cfg = load_config("demand", "supply.yaml")
    return (
        SupplyAssumptions(
            availability=a.get(cfg["availability"], "supply.availability"),
            target_utilisation=a.get(cfg["target_utilisation"], "supply.target_utilisation"),
            assumed_kw_when_unknown=a.get(
                cfg["assumed_kw_when_unknown"], "supply.assumed_kw_when_unknown"
            ),
            assumed_charge_points_when_unknown=int(
                a.get(
                    cfg["assumed_charge_points_when_unknown"],
                    "supply.assumed_charge_points_when_unknown",
                )
            ),
            new_charge_point_kw=a.get(cfg["new_charge_point_kw"], "supply.new_charge_point_kw"),
        ),
        a.placeholders_in_use,
    )


def charge_points_for_gap(gap_kwh: float) -> int:
    assumptions, _ = supply_assumptions()
    return additional_charge_points(gap_kwh, assumptions)


async def h3_layer(
    session: AsyncSession,
    year: int,
    scenario: str,
    column: str,
    region_id: str | None = None,
) -> tuple[list[str], list[float]]:
    allowed = {"public_kwh_p50", "ev_stock_p50", "gap_kwh", "access_ratio", "sessions_p50"}
    if column not in allowed:
        raise ValueError(f"column must be one of {sorted(allowed)}")
    rows = (
        await session.execute(
            text(
                f"select f.h3::text, f.{column} from h3_cell_feature f "
                "join region_cell c on c.h3 = f.h3 "
                "where c.region_id = :region_id and f.scenario = :scenario and f.year = :year "
                f"and f.{column} is not null"
            ),
            {"region_id": region_id or default_region_id(), "scenario": scenario, "year": year},
        )
    ).all()
    return [r[0] for r in rows], [round(float(r[1]), 3) for r in rows]
