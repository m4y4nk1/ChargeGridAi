"""geo.*, places.*, routing.*, chargers.* (Geo agent; chargers is read by several)."""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, Point, shape
from sqlalchemy import text

from app.agents.tools.base import Tool, ToolContext, ToolError
from app.agents.tools.common import Area, area_wkt, region_id
from app.core.regions import get_region
from app.providers.base import Point as GeoPoint
from app.providers.valhalla.routing import ValhallaRoutingProvider

_PLACE_RANK = {"city": 0, "town": 1, "suburb": 2, "village": 3, "neighbourhood": 4, "locality": 5}


class PlaceIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    region_id: str | None = None


async def _find_places(ctx: ToolContext, name: str, rid: str | None) -> list[dict[str, Any]]:
    b = get_region(rid).bbox
    async with ctx.db() as session:
        rows = (
            await session.execute(
                text(
                    "select name, place, population, ST_Y(geom), ST_X(geom), "
                    "(lower(name) = lower(:n) or alt_names ? :n) as exact "
                    "from osm_place where (name ilike :like or alt_names::text ilike :like) "
                    "and geom && ST_MakeEnvelope(:x0, :y0, :x1, :y1, 4326) limit 200"
                ),
                {
                    "n": name,
                    "like": f"%{name}%",
                    "x0": b.min_lng,
                    "y0": b.min_lat,
                    "x1": b.max_lng,
                    "y1": b.max_lat,
                },
            )
        ).all()
    ranked = sorted(
        rows, key=lambda r: (not r[5], _PLACE_RANK.get(r[1], 9), -(r[2] or 0), len(r[0]))
    )
    return [
        {
            "name": r[0],
            "place": r[1],
            "population": r[2],
            "lat": round(r[3], 5),
            "lng": round(r[4], 5),
        }
        for r in ranked[:5]
    ]


async def find_place(ctx: ToolContext, args: PlaceIn) -> dict[str, Any]:
    matches = await _find_places(ctx, args.name, region_id(ctx, args.region_id))
    if not matches:
        raise ToolError(f"No place named like '{args.name}' in the region's OSM gazetteer")
    return {"query": args.name, "matches": matches, "source": "OpenStreetMap place nodes"}


class CorridorIn(BaseModel):
    from_place: str = Field(min_length=2, max_length=100)
    to_place: str = Field(min_length=2, max_length=100)
    via: list[str] = Field(default_factory=list, max_length=3)
    buffer_m: int = Field(default=5000, ge=500, le=50000)
    label: str | None = Field(default=None, max_length=120)
    region_id: str | None = None


async def corridor(ctx: ToolContext, args: CorridorIn) -> dict[str, Any]:
    """Driving route between two places (Valhalla over OSM), stored as the session's
    corridor: candidates are later restricted to `buffer_m` around it."""
    rid = region_id(ctx, args.region_id)
    region = get_region(rid)
    stops = []
    for name in [args.from_place, *args.via, args.to_place]:
        found = await _find_places(ctx, name, rid)
        if not found:
            raise ToolError(f"Can't locate '{name}' inside {region.name}")
        stops.append(found[0])
    try:
        dist_m, dur_s, coords = await ValhallaRoutingProvider().route_line(
            [GeoPoint(lat=s["lat"], lng=s["lng"]) for s in stops]
        )
    except Exception as exc:  # routing engine down or no route
        raise ToolError(f"Routing failed: {exc}") from exc
    line = LineString(coords).simplify(0.0005, preserve_topology=False)
    geo = {
        "type": "LineString",
        "coordinates": [[round(x, 5), round(y, 5)] for x, y in line.coords],
    }
    label = args.label or f"{stops[0]['name']} – {stops[-1]['name']}"
    ctx.state["corridor"] = {"line": geo, "buffer_m": args.buffer_m, "label": label}
    ctx.state["region_id"] = rid
    await ctx.emit("progress", "geo", {"step": "corridor", "status": "completed", "label": label})
    return {
        "label": label,
        "from": stops[0],
        "to": stops[-1],
        "via": stops[1:-1],
        "distance_km": round(dist_m / 1000, 1),
        "duration_min": round(dur_s / 60),
        "buffer_m": args.buffer_m,
        "line_points": len(geo["coordinates"]),
        "region_id": rid,
        "source": "Valhalla route over OpenStreetMap",
        "summary": f"{label}: {dist_m / 1000:.0f} km, {dur_s / 60:.0f} min, "
        f"buffer {args.buffer_m} m",
    }


class IsochroneIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    minutes: list[int] = Field(default=[10], min_length=1, max_length=4)


async def isochrone(ctx: ToolContext, args: IsochroneIn) -> dict[str, Any]:
    fc = await ctx.get(
        "/isochrones", lat=args.lat, lng=args.lng, minutes=",".join(map(str, args.minutes))
    )
    out = []
    async with ctx.db() as session:
        for f in fc["features"]:
            geom = shape(f["geometry"])
            row = (
                await session.execute(
                    text(
                        "select ST_Area(ST_GeomFromText(:g, 4326)::geography) / 1e6, "
                        "(select count(*) from charging_station "
                        " where ST_Within(geom, ST_GeomFromText(:g, 4326)))"
                    ),
                    {"g": geom.wkt},
                )
            ).one()
            out.append(
                {
                    "minutes": f["properties"]["minutes"],
                    "area_km2": round(float(row[0]), 1),
                    "existing_stations": int(row[1]),
                }
            )
    return {"centre": {"lat": args.lat, "lng": args.lng}, "contours": out, "mode": "drive"}


_LAYERS = {
    "poi": ("poi", "category", "name"),
    "grid_asset": ("grid_asset", "type", "coalesce(voltage_kv::text, '')"),
    "road_segment": ("road_segment", "class", "coalesce(ref, name)"),
    "landcover": ("landcover", "class", "''"),
}


class QueryFeaturesIn(BaseModel):
    layer: Literal["poi", "grid_asset", "road_segment", "landcover"]
    area: Area = Field(default_factory=Area)
    kinds: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Filter on the layer's kind column: poi.category (e.g. FUEL, MALL), "
        "grid_asset.type (substation, line...), road_segment.class (motorway, trunk...)",
    )
    limit: int = Field(default=15, ge=0, le=50)


async def query_features(ctx: ToolContext, args: QueryFeaturesIn) -> dict[str, Any]:
    """Read-only, parameterised feature query over the OSM-derived layers."""
    table, kind_col, label = _LAYERS[args.layer]
    if args.layer == "landcover":
        async with ctx.db() as session:
            cols = {
                c
                for (c,) in (
                    await session.execute(
                        text(
                            "select column_name from information_schema.columns "
                            "where table_name = 'landcover'"
                        )
                    )
                ).all()
            }
        kind_col = next((c for c in ("class", "kind", "type", "landuse") if c in cols), "null")
    wkt = await area_wkt(ctx, args.area)
    params: dict[str, Any] = {"g": wkt, "limit": args.limit}
    where = "ST_Intersects(geom, ST_GeomFromText(:g, 4326))"
    if args.kinds:
        where += f" and {kind_col} = any(:kinds)"
        params["kinds"] = args.kinds
    async with ctx.db() as session:
        counts = (
            await session.execute(
                text(
                    f"select {kind_col}, count(*) from {table} where {where} group by 1 "
                    "order by 2 desc limit 20"
                ),
                params,
            )
        ).all()
        rows = (
            await session.execute(
                text(
                    f"select {kind_col}, {label}, ST_Y(ST_PointOnSurface(geom)), "
                    f"ST_X(ST_PointOnSurface(geom)) from {table} where {where} limit :limit"
                ),
                params,
            )
        ).all()
    return {
        "layer": args.layer,
        "counts": {str(k): int(n) for k, n in counts},
        "features": [
            {"kind": k, "label": lab or None, "lat": round(y, 5), "lng": round(x, 5)}
            for k, lab, y, x in rows
        ],
        "source": "OpenStreetMap (ODbL)",
    }


class NearbyIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: int = Field(default=1000, ge=50, le=5000)
    categories: list[str] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=15, ge=1, le=40)


async def search_nearby(ctx: ToolContext, args: NearbyIn) -> dict[str, Any]:
    """Nearby host POIs from the OSM layer (free, open licence). Paid place search
    (Google/Mappls) is not used by the agents."""
    params: dict[str, Any] = {
        "lat": args.lat,
        "lng": args.lng,
        "r": args.radius_m,
        "limit": args.limit,
    }
    where = "ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :r)"
    if args.categories:
        where += " and category = any(:cats)"
        params["cats"] = [c.upper() for c in args.categories]
    async with ctx.db() as session:
        rows = (
            await session.execute(
                text(
                    "select name, category, ST_Y(geom), ST_X(geom), "
                    "ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)"
                    f"::geography) d from poi where {where} order by d limit :limit"
                ),
                params,
            )
        ).all()
    return {
        "centre": {"lat": args.lat, "lng": args.lng},
        "radius_m": args.radius_m,
        "pois": [
            {
                "name": n,
                "category": c,
                "lat": round(y, 5),
                "lng": round(x, 5),
                "distance_m": round(d),
            }
            for n, c, y, x, d in rows
        ],
        "source": "OpenStreetMap (ODbL)",
        "paid_api_cost_inr": 0,
    }


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class MatrixIn(BaseModel):
    origins: list[LatLng] = Field(min_length=1, max_length=10)
    destinations: list[LatLng] = Field(min_length=1, max_length=10)
    traffic: bool = False


async def matrix(ctx: ToolContext, args: MatrixIn) -> dict[str, Any]:
    if args.traffic:
        raise ToolError(
            "Traffic-aware times need a paid routing API, which the agents don't call; "
            "use traffic=false (free-flow times from Valhalla)"
        )
    m = await ValhallaRoutingProvider().matrix(
        [GeoPoint(lat=p.lat, lng=p.lng) for p in args.origins],
        [GeoPoint(lat=p.lat, lng=p.lng) for p in args.destinations],
    )
    return {
        "minutes": [
            [round(s / 60, 1) if s is not None else None for s in row] for row in m.durations_s
        ],
        "km": [
            [round(d / 1000, 1) if d is not None else None for d in row] for row in m.distances_m
        ],
        "traffic": False,
        "source": "Valhalla (free-flow, OpenStreetMap)",
    }


class ChargersIn(BaseModel):
    area: Area = Field(default_factory=Area)
    min_kw: float | None = Field(default=None, ge=0)
    operator: str | None = None
    limit: int = Field(default=20, ge=0, le=50)


async def chargers_search(ctx: ToolContext, args: ChargersIn) -> dict[str, Any]:
    from app.db.repositories.stations import stations_geojson

    geom = shapely_wkt.loads(await area_wkt(ctx, args.area))
    async with ctx.db() as session:
        fc = await stations_geojson(session, geom.bounds, args.min_kw, args.operator)
    inside = [f for f in fc["features"] if geom.contains(Point(f["geometry"]["coordinates"]))]
    props = [
        f["properties"]
        | {"lng": f["geometry"]["coordinates"][0], "lat": f["geometry"]["coordinates"][1]}
        for f in inside
    ]
    bands = Counter(p["power_band"] for p in props)
    operators = Counter(p["operator"] or "unknown" for p in props)
    charge_points = sum(p["charge_points"] or 0 for p in props)
    return {
        "stations": len(props),
        "charge_points": charge_points,
        "by_power_band": dict(bands),
        "top_operators": dict(operators.most_common(8)),
        "min_kw_filter": args.min_kw,
        "list": [
            {
                "name": p["name"],
                "operator": p["operator"],
                "max_kw": p["max_kw"],
                "charge_points": p["charge_points"],
                "confidence": p["confidence"],
                "sources": p["sources"],
                "lat": round(p["lat"], 5),
                "lng": round(p["lng"], 5),
            }
            for p in sorted(props, key=lambda p: -(p["max_kw"] or 0))[: args.limit]
        ],
        "note": "Fused from OSM and Open Charge Map; counts are a floor (unlisted sites exist).",
        "summary": f"{len(props)} stations, {charge_points} charge points",
    }


TOOLS = [
    Tool(
        "geo.find_place",
        "geo",
        "Resolve a place name (city, town, suburb, village) to coordinates using the OSM "
        "gazetteer for the region. Returns up to 5 matches, best first.",
        PlaceIn,
        find_place,
    ),
    Tool(
        "geo.corridor",
        "geo",
        "Build a route corridor between two places (optionally via others) with Valhalla and "
        "store it as this session's corridor; candidate sites are then restricted to "
        "buffer_m around the route. Returns distance, drive time and the resolved places.",
        CorridorIn,
        corridor,
    ),
    Tool(
        "geo.isochrone",
        "geo",
        "Drive-time catchments (up to 4 contours, minutes) around a point: area in km2 and "
        "existing charging stations inside each.",
        IsochroneIn,
        isochrone,
    ),
    Tool(
        "geo.query_features",
        "geo",
        "Count and sample OSM features (poi, grid_asset, road_segment, landcover) in an "
        "area: a bbox, a circle (around), or the session corridor. Read-only.",
        QueryFeaturesIn,
        query_features,
    ),
    Tool(
        "places.search_nearby",
        "places",
        "Host POIs (fuel, mall, parking, restaurant, hotel, office park ...) near a point, "
        "nearest first, from OpenStreetMap. No paid API is called.",
        NearbyIn,
        search_nearby,
    ),
    Tool(
        "routing.matrix",
        "routing",
        "Free-flow drive times (minutes) and distances (km) between up to 10 origins and "
        "10 destinations (Valhalla).",
        MatrixIn,
        matrix,
    ),
    Tool(
        "chargers.search",
        "chargers",
        "Existing public charging stations in an area (bbox, circle or the session corridor; "
        "default: the region), optionally with a minimum power: totals, power bands, "
        "operators and a list of the most powerful.",
        ChargersIn,
        chargers_search,
    ),
]
