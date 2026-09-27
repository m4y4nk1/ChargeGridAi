from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_layers_are_all_openly_licensed_in_phase_1() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/layers")
    assert response.status_code == 200
    layers = response.json()
    assert {layer["id"] for layer in layers} == {
        "road_segment",
        "poi",
        "admin_boundary",
        "grid_asset",
    }
    assert all(layer["license_class"] == "OPEN" for layer in layers)
