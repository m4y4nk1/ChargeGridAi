"""Shared helpers for turning a source's socket/connector listing into EVSEs.

Sources describe charging capacity differently: OSM tags sockets per type,
OCM lists connections with quantities, and both may carry a separate count
of simultaneous charge points ("capacity" / "NumberOfPoints"). When the
charge-point count is known and unambiguous (one current type), it wins;
otherwise connector quantities stand in for charge points per current
type. That approximation is why every EVSE row keeps its connectors, so the
distinction between charge points and connectors stays inspectable.
"""

from dataclasses import dataclass

from app.providers.base import EVSE, Connector, ConnectorStandard


@dataclass(frozen=True)
class Socket:
    standard: ConnectorStandard
    quantity: int
    max_kw: float | None
    current: str | None  # AC | DC | None


_DC_STANDARDS = {
    ConnectorStandard.CCS2,
    ConnectorStandard.CHADEMO,
    ConnectorStandard.BHARAT_DC001,
    ConnectorStandard.GBT,
}
_AC_STANDARDS = {
    ConnectorStandard.TYPE2_AC,
    ConnectorStandard.TYPE1_AC,
    ConnectorStandard.BHARAT_AC001,
    ConnectorStandard.DOMESTIC_PLUG,
}


def current_for(standard: ConnectorStandard) -> str | None:
    if standard in _DC_STANDARDS:
        return "DC"
    if standard in _AC_STANDARDS:
        return "AC"
    return None


def evses_from_sockets(
    sockets: list[Socket], charge_points: int | None, id_prefix: str
) -> list[EVSE]:
    if not sockets:
        if charge_points:
            return [EVSE(id=f"{id_prefix}:unspecified", count=charge_points)]
        return []

    groups: dict[str | None, list[Socket]] = {}
    for s in sockets:
        groups.setdefault(s.current or current_for(s.standard), []).append(s)

    evses: list[EVSE] = []
    for current, members in sorted(groups.items(), key=lambda g: g[0] or ""):
        kws = [m.max_kw for m in members if m.max_kw is not None]
        count = (
            charge_points
            if charge_points and len(groups) == 1
            else sum(m.quantity for m in members)
        )
        evses.append(
            EVSE(
                id=f"{id_prefix}:{current or 'unknown'}",
                max_kw=max(kws) if kws else None,
                current=current,
                count=max(count, 1),
                connectors=[Connector(standard=m.standard, max_kw=m.max_kw) for m in members],
            )
        )
    return evses


def parse_kw(value: str | float | int | None) -> float | None:
    """'22 kW', '7.4kW', '50', 60 -> kW; watts-looking values (>= 1000 with no unit) -> kW."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value) if value > 0 else None
    text = value.strip().lower().replace(",", ".")
    number = "".join(ch for ch in text.split("kw")[0] if ch.isdigit() or ch == ".")
    try:
        kw = float(number)
    except ValueError:
        return None
    if "kw" not in text and kw >= 1000:
        kw /= 1000
    return kw if kw > 0 else None
