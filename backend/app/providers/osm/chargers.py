"""OSM `amenity=charging_station` -> normalised ChargingStation.

Reads from the `poi` table written by the osm2pgsql flex import, whose
`attrs` column holds every original OSM tag.
"""

from datetime import datetime

from app.providers.base import ChargingStation, ConnectorStandard, LicenseClass, Point
from app.providers.charger_normalize import Socket, evses_from_sockets, parse_kw

# OSM socket:* keys (wiki: Key:socket) -> our connector standards.
_SOCKET_KEYS: dict[str, ConnectorStandard] = {
    "type2_combo": ConnectorStandard.CCS2,
    "type2": ConnectorStandard.TYPE2_AC,
    "type2_cable": ConnectorStandard.TYPE2_AC,
    "chademo": ConnectorStandard.CHADEMO,
    "type1": ConnectorStandard.TYPE1_AC,
    "type1_combo": ConnectorStandard.UNKNOWN,
    "gb_t": ConnectorStandard.GBT,
    "gb_ac": ConnectorStandard.GBT,
    "gb_dc": ConnectorStandard.GBT,
    "bharat_ac001": ConnectorStandard.BHARAT_AC001,
    "bharat_dc001": ConnectorStandard.BHARAT_DC001,
    "schuko": ConnectorStandard.DOMESTIC_PLUG,
    "bs1363": ConnectorStandard.DOMESTIC_PLUG,
    "as3112": ConnectorStandard.DOMESTIC_PLUG,
    "indian": ConnectorStandard.DOMESTIC_PLUG,
}

# OSM `capacity` on a charging station is the number of vehicles that can
# charge at once. Values above this are almost always a kW figure or a car
# park's capacity typed into the wrong tag, so they're treated as unknown and
# reported, never used as a charge-point count.
MAX_PLAUSIBLE_CHARGE_POINTS = 20

_ACCESS = {
    "yes": "public",
    "public": "public",
    "customers": "semi",
    "permissive": "semi",
    "private": "private",
    "no": "private",
}


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        n = int(value.strip())
    except ValueError:
        return None
    return n if n > 0 else None


def capacity_issue(tags: dict[str, str]) -> str | None:
    """Why this station's `capacity` tag can't be trusted, or None if it's fine/absent."""
    raw = tags.get("capacity")
    if raw is None:
        return None
    n = _to_int(raw)
    if n is None:
        return f"capacity={raw!r} is not a whole number"
    if n > MAX_PLAUSIBLE_CHARGE_POINTS:
        return f"capacity={n} exceeds {MAX_PLAUSIBLE_CHARGE_POINTS} (likely kW or car-park size)"
    return None


def osm_tags_to_station(
    osm_type: str, osm_id: int, tags: dict[str, str], lat: float, lng: float, retrieved_at: datetime
) -> ChargingStation:
    sockets: list[Socket] = []
    for key, standard in _SOCKET_KEYS.items():
        raw = tags.get(f"socket:{key}")
        if raw is None or raw == "no":
            continue
        sockets.append(
            Socket(
                standard=standard,
                quantity=_to_int(raw) or 1,
                max_kw=parse_kw(tags.get(f"socket:{key}:output")),
                current=None,
            )
        )

    record_id = f"{osm_type}{osm_id}"
    return ChargingStation(
        id=record_id,
        source="osm",
        license_class=LicenseClass.OPEN,
        retrieved_at=retrieved_at,
        confidence="MEDIUM",
        name=tags.get("name"),
        operator=tags.get("operator") or tags.get("brand") or tags.get("network"),
        location=Point(lat=lat, lng=lng),
        evses=evses_from_sockets(
            sockets,
            None if capacity_issue(tags) else _to_int(tags.get("capacity")),
            record_id,
        ),
        address=", ".join(
            v for k in ("addr:housenumber", "addr:street", "addr:city") if (v := tags.get(k))
        )
        or None,
        access=_ACCESS.get(tags.get("access", ""), None),
        opening_hours=tags.get("opening_hours"),
    )
