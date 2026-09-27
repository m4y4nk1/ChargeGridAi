"""Scenarios, planning runs, candidate sites, feasibility and scores (Section 8.2)."""

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.common import TimestampMixin


class Scenario(TimestampMixin, Base):
    __tablename__ = "scenario"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    region_id: Mapped[str] = mapped_column(Text)
    target_year: Mapped[int] = mapped_column(Integer)
    adoption_case: Mapped[str] = mapped_column(Text)  # slow | base | fast
    adoption_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    charger_classes: Mapped[list[str]] = mapped_column(ARRAY(Text))  # e.g. DC_60, AC_22
    segments: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    budget_inr: Mapped[int | None] = mapped_column(BigInteger)
    weight_profile_id: Mapped[str | None] = mapped_column(Text)
    # user_sites: [{name, lat, lng}], plus any per-scenario overrides
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="SET NULL"), index=True
    )


class PlanningRun(TimestampMixin, Base):
    __tablename__ = "planning_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenario.id"), index=True)
    status: Mapped[str] = mapped_column(Text, default="queued")  # queued|running|succeeded|failed
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Resolved configuration and parameters the run actually used (reproducibility).
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    snapshot_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    model_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    git_sha: Mapped[str | None] = mapped_column(Text)
    # Ordered step log: [{step, status, at, detail}]
    # Celery task executing the run; lets the API spot a worker that died mid-run.
    task_id: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    funnel: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class CandidateSite(TimestampMixin, Base):
    __tablename__ = "candidate_site"
    __table_args__ = (Index("ix_candidate_site_run_status", "created_by_run", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by_run: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("planning_run.id", ondelete="CASCADE")
    )
    region_id: Mapped[str] = mapped_column(Text)
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))
    origin: Mapped[str] = mapped_column(Text)  # poi | corridor | gap_cell | user
    # Other origins that proposed (nearly) the same spot and were merged in.
    merged_origins: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    host_poi_id: Mapped[str | None] = mapped_column(Text)
    host_type: Mapped[str] = mapped_column(Text)  # FUEL_STATION ... or ROADSIDE when unhosted
    name: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="proposed")  # proposed|feasible|rejected


class FeasibilityResult(TimestampMixin, Base):
    __tablename__ = "feasibility_result"

    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_site.id", ondelete="CASCADE"), primary_key=True
    )
    passed: Mapped[bool] = mapped_column(Boolean)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # Per rule: {code: {outcome, measured, threshold, detail}}
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SiteFeature(TimestampMixin, Base):
    """Every measured input to feasibility and (from Phase 5) scoring."""

    __tablename__ = "site_feature"

    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_site.id", ondelete="CASCADE"), primary_key=True
    )
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario: Mapped[str] = mapped_column(Text, primary_key=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB)
    snapshot_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)


class SiteCatchment(TimestampMixin, Base):
    """A candidate's drive-time catchment (Valhalla isochrone) and the H3 cells it covers.

    Scoring (Phase 5) reads it and optimisation (Phase 6) reuses it as the set of
    demand cells a site can serve.
    """

    __tablename__ = "site_catchment"

    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_site.id", ondelete="CASCADE"), primary_key=True
    )
    minutes: Mapped[int] = mapped_column(Integer)
    geom: Mapped[Any] = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=False))
    h3_cells: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)

    __table_args__ = (Index("site_catchment_geom_idx", "geom", postgresql_using="gist"),)


class RunSite(TimestampMixin, Base):
    """A candidate's result within a run: scores and rank now; selection, coverage and
    why-not from optimisation (Phase 6)."""

    __tablename__ = "run_site"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("planning_run.id", ondelete="CASCADE"), primary_key=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_site.id", ondelete="CASCADE"), primary_key=True
    )
    rank: Mapped[int | None] = mapped_column(Integer)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    phase_year: Mapped[int | None] = mapped_column(Integer)
    score_total: Mapped[float | None] = mapped_column(Float)
    # {sub_score: 0-100} under the run's weight profile.
    sub_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    robustness: Mapped[float | None] = mapped_column(Float)
    coverage_kwh: Mapped[float | None] = mapped_column(Float)
    served_population: Mapped[float | None] = mapped_column(Float)
    why_not: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Raw measurements behind each sub-score, catchment indices vs benchmark, POI counts.
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    __table_args__ = (Index("run_site_run_rank_idx", "run_id", "rank"),)


class Optimisation(TimestampMixin, Base):
    """One solve of Model A (Section 9.7) for a run: the scenario's base plan, a what-if,
    a module C strategy, or a Pareto point."""

    __tablename__ = "optimisation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("planning_run.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(Text)  # base | whatif | strategy | pareto
    strategy: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(Text)
    solver_stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # KPIs: coverage_kwh, demand_kwh, coverage_share, cost_inr, n_sites, n_charge_points,
    # equity, utilisation, charger_mix.
    kpis: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    on_front: Mapped[bool] = mapped_column(Boolean, default=False)
    # What-if only: sites added/removed and KPI deltas against the base plan.
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # h3 -> {site_id, served_kwh}: which open site serves each cell (module D).
    cell_assignment: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Banded drive-time Voronoi, computed on first request (module D).
    drive_time_voronoi: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class OptimisationSite(Base):
    __tablename__ = "optimisation_site"

    optimisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("optimisation.id", ondelete="CASCADE"), primary_key=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_site.id", ondelete="CASCADE"), primary_key=True
    )
    bundle: Mapped[str] = mapped_column(Text)  # e.g. DC_60x4
    charge_points: Mapped[int] = mapped_column(Integer)
    cost_inr: Mapped[float] = mapped_column(Float)
    capacity_kwh: Mapped[float] = mapped_column(Float)
    served_kwh: Mapped[float] = mapped_column(Float)
    served_population: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SiteConfig(TimestampMixin, Base):
    """A planned site's charger sizing (Section 9.8) and finance (Section 9.10), computed
    once per optimisation, site and subsidy setting."""

    __tablename__ = "site_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    optimisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("optimisation.id", ondelete="CASCADE"), index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_site.id", ondelete="CASCADE"))
    subsidy: Mapped[bool] = mapped_column(Boolean, default=False)
    sizing: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    finance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    placeholders: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)

    __table_args__ = (
        Index("site_config_unique_idx", "optimisation_id", "site_id", "subsidy", unique=True),
    )
