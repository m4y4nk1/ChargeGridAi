# 0001: Core technology stack

## Status
Accepted

## Context
ChargeGrid AI needs a geospatial analytics backend, a deterministic
forecasting/optimisation core, an LLM agent layer, and a data-dense mapping
frontend, all deliverable by a small team on a single-region (Pune) MVP that
must later scale to national coverage. The full rationale and comparison
table live in the project brief (`ChargeGrid_AI_Master_Dev_Prompt.md`,
Section 4); this ADR records the decision, not the full survey.

## Decision
- **Backend:** Python 3.12, FastAPI + Pydantic v2, SQLAlchemy 2.x (async) +
  GeoAlchemy2, Alembic, asyncpg. Python is the natural fit for the
  geospatial/scientific stack (GeoPandas, OR-Tools, statsmodels, pymoo) the
  deterministic engines depend on, so keeping API, workers, and engines in one
  language avoids a serialization boundary between "the part that decides"
  and "the part that computes."
- **Data:** PostgreSQL 16 + PostGIS 3.4+, with h3-pg and pgvector extensions
  in the same database — one transactional/spatial/vector store instead of
  three, since MVP scale (Pune) doesn't yet justify splitting them.
- **Jobs:** Celery + Redis for the MVP job runner (see ADR 0005).
- **Frontend:** React 19 + TypeScript + Vite, Tailwind v4 + shadcn/ui,
  Google Maps JS + deck.gl as the primary map canvas with a MapLibre/OSM
  fallback mode (see ADR 0002), Apache ECharts for charts that need to
  render large series and uncertainty bands cleanly.
- **Agents:** LangGraph for orchestration, with a provider-agnostic LLM
  interface (see ADR 0004).

## Consequences
- One backend language means engine code, provider adapters, and API
  handlers share types and can be unit-tested the same way; no gRPC/REST hop
  between "compute" and "serve."
- PostGIS+h3-pg+pgvector-in-one-database means no managed pgvector service
  is required, but it does mean a custom Postgres image (see
  `infra/docker/postgres/Dockerfile`) since no off-the-shelf image bundles
  all three extensions.
- Locking into Google Maps JS as the primary canvas creates the licensing
  obligations detailed in ADR 0002 and Section 7.4 of the brief.
