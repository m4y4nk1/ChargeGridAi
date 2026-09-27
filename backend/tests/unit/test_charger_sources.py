from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.providers.base import BBox
from app.providers.charger_normalize import Socket, evses_from_sockets, parse_kw
from app.providers.government.bee_bhel import GovernmentCsvError, load_government_csv
from app.providers.ocm import client as ocm
from app.providers.osm.chargers import capacity_issue, osm_tags_to_station

NOW = datetime(2026, 9, 24, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("22 kW", 22.0),
        ("7.4kW", 7.4),
        ("50", 50.0),
        (60, 60.0),
        ("22000", 22.0),
        ("0", None),
        (None, None),
        ("fast", None),
    ],
)
def test_parse_kw(value: object, expected: float | None) -> None:
    assert parse_kw(value) == expected  # type: ignore[arg-type]


def test_charge_point_count_wins_when_unambiguous() -> None:
    sockets = [Socket("CCS2", 2, 60.0, None), Socket("CHADEMO", 1, 50.0, None)]  # type: ignore[arg-type]
    [evse] = evses_from_sockets(sockets, charge_points=2, id_prefix="x")
    assert (evse.current, evse.count, evse.max_kw) == ("DC", 2, 60.0)
    assert len(evse.connectors) == 2


def test_connector_quantities_stand_in_when_ac_and_dc_are_mixed() -> None:
    sockets = [Socket("CCS2", 1, 60.0, None), Socket("TYPE2_AC", 2, 22.0, None)]  # type: ignore[arg-type]
    evses = evses_from_sockets(sockets, charge_points=2, id_prefix="x")
    assert {(e.current, e.count) for e in evses} == {("AC", 2), ("DC", 1)}


def test_osm_tags_map_to_connectors_power_and_access() -> None:
    station = osm_tags_to_station(
        "N",
        42,
        {
            "name": "EZ Charge",
            "operator": "Tata Power",
            "socket:type2_combo": "2",
            "socket:type2_combo:output": "60 kW",
            "socket:type2": "1",
            "socket:type2:output": "22 kW",
            "access": "customers",
        },
        18.52,
        73.85,
        NOW,
    )
    assert station.id == "N42" and station.source == "osm" and station.license_class == "OPEN"
    assert station.access == "semi"
    by_current = {e.current: e for e in station.evses}
    assert by_current["DC"].count == 2 and by_current["DC"].max_kw == 60
    assert by_current["AC"].connectors[0].standard == "TYPE2_AC"


def test_osm_station_without_socket_tags_has_no_invented_evses() -> None:
    station = osm_tags_to_station("N", 1, {"amenity": "charging_station"}, 18.5, 73.8, NOW)
    assert station.evses == []


REFERENCE = {
    "ConnectionTypes": [
        {"ID": 33, "Title": "CCS (Type 2)"},
        {"ID": 25, "Title": "Type 2 (Socket Only)"},
    ],
    "CurrentTypes": [{"ID": 30, "Title": "DC"}, {"ID": 10, "Title": "AC (Single-Phase)"}],
    "Operators": [{"ID": 7, "Title": "Tata Power"}],
    "UsageTypes": [{"ID": 1, "Title": "Public"}],
    "StatusTypes": [{"ID": 50, "Title": "Operational"}],
    "DataProviders": [
        {
            "ID": 1,
            "License": "Licensed under Creative Commons Attribution 4.0 International (CC BY 4.0)",
        },
        {"ID": 99, "License": "Proprietary; redistribution not permitted"},
    ],
}


def _poi(poi_id: int, provider: int = 1) -> dict[str, object]:
    return {
        "ID": poi_id,
        "DataProviderID": provider,
        "OperatorID": 7,
        "UsageTypeID": 1,
        "StatusTypeID": 50,
        "NumberOfPoints": 2,
        "AddressInfo": {"Title": f"Site {poi_id}", "Latitude": 18.5, "Longitude": 73.8},
        "Connections": [
            {"ConnectionTypeID": 33, "CurrentTypeID": 30, "PowerKW": 60, "Quantity": 2}
        ],
    }


async def test_ocm_fetch_normalises_and_drops_non_open_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "k"
        if request.url.path.endswith("/referencedata"):
            return httpx.Response(200, json=REFERENCE)
        return httpx.Response(200, json=[_poi(1), _poi(2, provider=99)])

    provider = ocm.OCMChargerProvider(
        "k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    result = await provider.fetch_region(
        BBox(min_lat=18.3, min_lng=73.5, max_lat=18.8, max_lng=74.1)
    )

    assert result.dropped_non_open == 1
    [station] = result.stations
    assert (station.operator, station.access, station.status) == (
        "Tata Power",
        "public",
        "Operational",
    )
    [evse] = station.evses
    assert (evse.current, evse.count, evse.max_kw) == ("DC", 2, 60.0)
    assert evse.connectors[0].standard == "CCS2"


async def test_ocm_splits_the_bbox_when_results_are_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ocm, "MAX_RESULTS", 2)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/referencedata"):
            return httpx.Response(200, json=REFERENCE)
        calls.append(request.url.params["boundingbox"])
        return httpx.Response(
            200, json=[_poi(1), _poi(2)] if len(calls) == 1 else [_poi(len(calls) + 10)]
        )

    provider = ocm.OCMChargerProvider(
        "k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    result = await provider.fetch_region(
        BBox(min_lat=18.3, min_lng=73.5, max_lat=18.8, max_lng=74.1)
    )
    assert len(calls) == 5  # the full box, then four quadrants
    assert len(result.stations) == 4


def test_ocm_requires_a_key() -> None:
    with pytest.raises(ocm.OCMNotConfigured):
        ocm.OCMChargerProvider("")


def test_government_csv_import(tmp_path: Path) -> None:
    csv_path = tmp_path / "bee.csv"
    csv_path.write_text(
        "Station ID,Station Name,Operator Name,Latitude,Longitude,"
        "Connector Type,Power (kW),No. of Chargers\n"
        "G1,Swargate Depot,MSEDCL,18.50,73.86,CCS (Type 2),60,2\n"
    )
    [station] = load_government_csv(csv_path, "bee_evyatra", NOW)
    assert station.license_class == "GOVERNMENT" and station.confidence == "HIGH"
    assert station.evses[0].count == 2 and station.evses[0].current == "DC"


def test_government_csv_missing_coordinates_fails_loudly(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("Station ID,Station Name\nG1,Depot\n")
    with pytest.raises(GovernmentCsvError, match="latitude"):
        load_government_csv(csv_path, "bee_evyatra", NOW)


@pytest.mark.parametrize(
    ("capacity", "count", "flagged"),
    [("2", 2, False), ("60", None, True), ("30 kw", None, True)],
)
def test_implausible_osm_capacity_is_unknown_and_flagged(
    capacity: str, count: int | None, flagged: bool
) -> None:
    tags = {"amenity": "charging_station", "capacity": capacity}
    station = osm_tags_to_station("N", 1, tags, 18.5, 73.8, NOW)
    assert (station.evses[0].count if station.evses else None) == count
    assert (capacity_issue(tags) is not None) == flagged
