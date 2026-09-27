"""GET /layers (Section 11): layer catalogue with licence class and attribution.

Phase 1 has exactly the OSM-derived layers from the foundation import; the
full `data_source`-backed catalogue (Section 8.1) lands as sources are
added from Phase 2 onward. Every entry here is OPEN/OSM — nothing
RESTRICTED_* exists in this app yet, since no Google/Mappls/TomTom adapter
has been wired to a real key.
"""

from fastapi import APIRouter

from app.core.settings import get_settings
from app.schemas.layer import LayerInfo

router = APIRouter(tags=["layers"])


@router.get("/layers", response_model=list[LayerInfo])
async def get_layers() -> list[LayerInfo]:
    martin_url = get_settings().martin_url
    osm_attribution = "© OpenStreetMap contributors (ODbL)"
    return [
        LayerInfo(
            id="road_segment",
            label="Roads",
            license_class="OPEN",
            attribution=osm_attribution,
            tile_url_template=f"{martin_url}/road_segment/{{z}}/{{x}}/{{y}}",
        ),
        LayerInfo(
            id="poi",
            label="Points of interest",
            license_class="OPEN",
            attribution=osm_attribution,
            tile_url_template=f"{martin_url}/poi/{{z}}/{{x}}/{{y}}",
        ),
        LayerInfo(
            id="admin_boundary",
            label="Administrative boundaries",
            license_class="OPEN",
            attribution=osm_attribution,
            tile_url_template=f"{martin_url}/admin_boundary/{{z}}/{{x}}/{{y}}",
        ),
        LayerInfo(
            id="grid_asset",
            label="Grid assets (proximity proxy)",
            license_class="OPEN",
            attribution=osm_attribution,
            tile_url_template=f"{martin_url}/grid_asset/{{z}}/{{x}}/{{y}}",
        ),
    ]
