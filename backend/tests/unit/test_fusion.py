from datetime import UTC, datetime

import pytest

from app.ingestion.fusion import fuse, match_score, trigram_similarity
from app.providers.base import EVSE, ChargingStation, Connector, LicenseClass, Point

NOW = datetime(2026, 9, 24, tzinfo=UTC)
# Deccan Gymkhana, Pune; 0.0001 deg lat ~= 11 m.
BASE_LAT, BASE_LNG = 18.5167, 73.8412


def station(
    source: str,
    record_id: str,
    dlat_m: float = 0.0,
    *,
    name: str | None = None,
    operator: str | None = None,
    evses: list[EVSE] | None = None,
    license_class: LicenseClass = LicenseClass.OPEN,
) -> ChargingStation:
    return ChargingStation(
        id=record_id,
        source=source,
        license_class=license_class,
        retrieved_at=NOW,
        confidence="MEDIUM",
        name=name,
        operator=operator,
        location=Point(lat=BASE_LAT + dlat_m / 111_320, lng=BASE_LNG),
        evses=evses or [],
    )


def dc(kw: float, count: int = 1) -> EVSE:
    return EVSE(
        id=f"dc{kw}",
        max_kw=kw,
        current="DC",
        count=count,
        connectors=[Connector(standard="CCS2", max_kw=kw)],
    )


def test_trigram_similarity_matches_pg_trgm_semantics() -> None:
    assert trigram_similarity("Tata Power", "tata power") == 1.0
    assert trigram_similarity("Tata Power", None) is None
    sim = trigram_similarity("Tata Power EZ Charge", "Tata Power")
    assert sim is not None and 0.4 < sim < 1.0


def test_same_station_in_two_sources_fuses_into_one() -> None:
    fused = fuse(
        [
            station("osm", "n1", name="Tata Power EZ Charge", operator="Tata Power"),
            station(
                "ocm",
                "123",
                dlat_m=30,
                name="Tata Power - Deccan",
                operator="Tata Power",
                evses=[dc(60, count=2)],
            ),
        ]
    )
    assert len(fused) == 1
    assert {m.source for m, _ in fused[0].members} == {"osm", "ocm"}
    assert fused[0].confidence == "HIGH"


def test_records_from_the_same_source_never_merge() -> None:
    fused = fuse(
        [station("ocm", "1", name="Statiq"), station("ocm", "2", dlat_m=10, name="Statiq")]
    )
    assert len(fused) == 2


def test_different_known_operators_at_the_same_site_stay_separate() -> None:
    fused = fuse(
        [
            station("osm", "n1", operator="Tata Power"),
            station("ocm", "9", dlat_m=5, operator="Statiq"),
        ]
    )
    assert len(fused) == 2


def test_records_beyond_200m_never_match() -> None:
    assert match_score(station("osm", "a"), station("ocm", "b", dlat_m=250)) is None


def test_government_record_wins_on_counts_and_power_and_conflict_is_recorded() -> None:
    fused = fuse(
        [
            station("ocm", "5", name="Mall charger", evses=[dc(60, count=2)]),
            station(
                "bee_evyatra",
                "G7",
                dlat_m=20,
                name="Mall charger",
                evses=[dc(120, count=4)],
                license_class=LicenseClass.GOVERNMENT,
            ),
        ]
    )
    assert len(fused) == 1
    [f] = fused
    assert sum(e.count for e in f.evses) == 4  # taken from the government record, not summed to 6
    assert f.evses[0].max_kw == 120
    fields = {c["field"] for c in f.conflicts}
    assert {"charge_point_count", "max_kw"} <= fields


def test_fused_station_id_is_stable_across_runs() -> None:
    records = [station("osm", "n1", name="X"), station("ocm", "1", dlat_m=10, name="X")]
    assert fuse(records)[0].id == fuse(list(reversed(records)))[0].id


def test_google_records_are_rejected_from_the_fused_store() -> None:
    with pytest.raises(ValueError):
        fuse([station("google_places", "ChIJ", license_class=LicenseClass.RESTRICTED_GOOGLE)])


def test_fused_license_is_the_most_restrictive_member() -> None:
    [f] = fuse(
        [
            station("osm", "n1", name="X"),
            station(
                "bee_evyatra", "G1", dlat_m=10, name="X", license_class=LicenseClass.GOVERNMENT
            ),
        ]
    )
    assert f.license_class == LicenseClass.GOVERNMENT
