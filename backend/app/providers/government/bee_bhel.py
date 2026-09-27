"""Government public-charger registry importer (Section 7.1 row 5).

Neither BEE EV Yatra nor the BHEL PM E-DRIVE portal offers a public API or
bulk export (docs/verification.md item 5), and the EV Yatra site was not
reachable at all during Phase 2 — so this reads a manually exported CSV.
The exact export layout isn't verified, so the expected columns are defined
here (case-insensitive, with common aliases); anything else fails loudly
rather than being guessed at.
"""

import csv
from datetime import datetime
from pathlib import Path

from app.providers.base import ChargingStation, ConnectorStandard, LicenseClass, Point
from app.providers.charger_normalize import Socket, current_for, evses_from_sockets, parse_kw
from app.providers.ocm.client import connection_standard

REQUIRED_COLUMNS = {"station_id", "station_name", "latitude", "longitude"}
_ALIASES = {
    "id": "station_id",
    "station id": "station_id",
    "name": "station_name",
    "station name": "station_name",
    "lat": "latitude",
    "lng": "longitude",
    "lon": "longitude",
    "long": "longitude",
    "operator name": "operator",
    "cpo": "operator",
    "no. of chargers": "num_chargers",
    "number of chargers": "num_chargers",
    "charger type": "connector",
    "connector type": "connector",
    "power (kw)": "power_kw",
    "capacity (kw)": "power_kw",
    "power": "power_kw",
}


class GovernmentCsvError(ValueError):
    pass


def _canonical(header: str) -> str:
    key = header.strip().lower()
    return _ALIASES.get(key, key.replace(" ", "_"))


def load_government_csv(
    path: Path, source_id: str, retrieved_at: datetime
) -> list[ChargingStation]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise GovernmentCsvError(f"{path} has no header row")
        columns = {_canonical(h): h for h in reader.fieldnames}
        missing = REQUIRED_COLUMNS - columns.keys()
        if missing:
            raise GovernmentCsvError(f"{path} is missing required columns: {sorted(missing)}")

        stations = []
        for line_no, row in enumerate(reader, start=2):
            values = {key: (row.get(header) or "").strip() for key, header in columns.items()}
            get = values.get
            try:
                lat, lng = float(get("latitude", "")), float(get("longitude", ""))
            except ValueError as exc:
                raise GovernmentCsvError(f"{path}:{line_no} has an invalid coordinate") from exc

            standard = (
                connection_standard(get("connector", ""))
                if get("connector", "")
                else ConnectorStandard.UNKNOWN
            )
            kw = parse_kw(get("power_kw", "") or None)
            chargers = int(get("num_chargers", "")) if get("num_chargers", "").isdigit() else None
            sockets = (
                [
                    Socket(
                        standard=standard,
                        quantity=chargers or 1,
                        max_kw=kw,
                        current=current_for(standard),
                    )
                ]
                if (get("connector", "") or kw)
                else []
            )
            record_id = get("station_id", "")
            stations.append(
                ChargingStation(
                    id=record_id,
                    source=source_id,
                    license_class=LicenseClass.GOVERNMENT,
                    retrieved_at=retrieved_at,
                    confidence="HIGH",
                    name=get("station_name", "") or None,
                    operator=get("operator", "") or None,
                    location=Point(lat=lat, lng=lng),
                    evses=evses_from_sockets(sockets, chargers, f"{source_id}:{record_id}"),
                    address=get("address", "") or None,
                    access="public",
                )
            )
        return stations
