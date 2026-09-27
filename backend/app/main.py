from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api.v1.admin import router as admin_router
from app.api.v1.agents import router as agents_router
from app.api.v1.auth import me_router
from app.api.v1.auth import router as auth_router
from app.api.v1.data_sources import router as data_sources_router
from app.api.v1.demand import router as demand_router
from app.api.v1.evidence import router as evidence_router
from app.api.v1.exports import router as exports_router
from app.api.v1.google import router as google_router
from app.api.v1.grid import router as grid_router
from app.api.v1.isochrones import router as isochrones_router
from app.api.v1.layers import router as layers_router
from app.api.v1.optimisation import router as optimisation_router
from app.api.v1.planning import router as planning_router
from app.api.v1.readiness import router as readiness_router
from app.api.v1.regions import router as regions_router
from app.api.v1.stations import router as stations_router
from app.api.v1.twin import router as twin_router
from app.core.auth import org_guard, require
from app.core.middleware import LoginRateLimit, SecurityHeaders
from app.core.settings import get_settings

app = FastAPI(title="ChargeGrid AI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Org"],
    expose_headers=["Content-Disposition"],
)
app.add_middleware(SecurityHeaders)
app.add_middleware(LoginRateLimit)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(me_router, prefix="/api/v1")
app.include_router(regions_router, prefix="/api/v1", dependencies=[require("viewer")])
app.include_router(isochrones_router, prefix="/api/v1", dependencies=[require("viewer")])
app.include_router(layers_router, prefix="/api/v1", dependencies=[require("viewer")])
app.include_router(stations_router, prefix="/api/v1", dependencies=[require("viewer")])
app.include_router(data_sources_router, prefix="/api/v1", dependencies=[require("viewer")])
app.include_router(evidence_router, prefix="/api/v1", dependencies=[require("viewer")])
app.include_router(demand_router, prefix="/api/v1", dependencies=[require("viewer")])
app.include_router(planning_router, prefix="/api/v1", dependencies=[Depends(org_guard)])
app.include_router(optimisation_router, prefix="/api/v1", dependencies=[Depends(org_guard)])
app.include_router(agents_router, prefix="/api/v1", dependencies=[Depends(org_guard)])
app.include_router(grid_router, prefix="/api/v1", dependencies=[Depends(org_guard)])
app.include_router(admin_router, prefix="/api/v1")
app.include_router(google_router, prefix="/api/v1", dependencies=[require("viewer")])
app.include_router(readiness_router, prefix="/api/v1", dependencies=[require("viewer")])
app.include_router(twin_router, prefix="/api/v1", dependencies=[Depends(org_guard)])
app.include_router(exports_router, prefix="/api/v1", dependencies=[Depends(org_guard)])


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "env": settings.app_env}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    return "# Prometheus metrics wiring lands with observability in a later phase\n"
