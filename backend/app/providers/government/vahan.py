"""VAHAN registration importer (Section 7.1 row 3; docs/verification.md item 4).

No documented bulk export of monthly RTO-level data exists, so this reads a
normalised CSV prepared from manual Vahan dashboard exports:

    rto_code,month,fuel,vehicle_class,count
    MH12,2025-07,ELECTRIC(BOV),M-CYCLE/SCOOTER,1234

A row whose vehicle_class is TOTAL is a checksum, not data: the importer
verifies it equals the sum of that (rto, month, fuel)'s class rows and then
drops it. Unknown vehicle classes fail the import rather than being
silently bucketed — the mapping lives in config/demand/segments.yaml.
"""

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

REQUIRED_COLUMNS = ("rto_code", "month", "fuel", "vehicle_class", "count")


class VahanImportError(ValueError):
    pass


@dataclass(frozen=True)
class RegistrationRow:
    rto_code: str
    month: date
    fuel: str
    vehicle_class: str
    segment: str
    ev_type: str | None
    count: int


@dataclass(frozen=True)
class VahanMappings:
    vehicle_class_to_segment: dict[str, str]
    fuel_to_ev_type: dict[str, str]

    @classmethod
    def load(cls, segments_yaml: Path) -> "VahanMappings":
        data = yaml.safe_load(segments_yaml.read_text())
        return cls(
            vehicle_class_to_segment={
                k.upper(): v for k, v in data["vahan_vehicle_class_map"].items()
            },
            fuel_to_ev_type={k.upper(): v for k, v in data["vahan_ev_fuel_map"].items()},
        )


def _parse_month(value: str, line_no: int) -> date:
    try:
        year, month = value.strip().split("-")[:2]
        return date(int(year), int(month), 1)
    except ValueError as exc:
        raise VahanImportError(f"line {line_no}: month {value!r} is not YYYY-MM") from exc


def load_vahan_csv(path: Path, mappings: VahanMappings) -> list[RegistrationRow]:
    rows: dict[tuple[str, date, str, str], RegistrationRow] = {}
    totals: dict[tuple[str, date, str], tuple[int, int]] = {}

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise VahanImportError(f"{path} is missing columns: {sorted(missing)}")

        for line_no, raw in enumerate(reader, start=2):
            rto = raw["rto_code"].strip().upper()
            month = _parse_month(raw["month"], line_no)
            fuel = raw["fuel"].strip().upper()
            vehicle_class = raw["vehicle_class"].strip().upper()
            try:
                count = int(raw["count"].replace(",", "").strip())
            except ValueError as exc:
                raise VahanImportError(
                    f"line {line_no}: count {raw['count']!r} is not an integer"
                ) from exc
            if count < 0:
                raise VahanImportError(f"line {line_no}: negative count")

            if vehicle_class == "TOTAL":
                totals[(rto, month, fuel)] = (count, line_no)
                continue

            segment = mappings.vehicle_class_to_segment.get(vehicle_class)
            if segment is None:
                raise VahanImportError(
                    f"line {line_no}: unmapped vehicle class {vehicle_class!r} — "
                    "add it to vahan_vehicle_class_map in config/demand/segments.yaml"
                )
            key = (rto, month, fuel, vehicle_class)
            if key in rows:
                raise VahanImportError(f"line {line_no}: duplicate row for {key}")
            rows[key] = RegistrationRow(
                rto_code=rto,
                month=month,
                fuel=fuel,
                vehicle_class=vehicle_class,
                segment=segment,
                ev_type=mappings.fuel_to_ev_type.get(fuel),
                count=count,
            )

    for (rto, month, fuel), (expected, line_no) in totals.items():
        actual = sum(
            r.count for r in rows.values() if (r.rto_code, r.month, r.fuel) == (rto, month, fuel)
        )
        if actual != expected:
            raise VahanImportError(
                f"line {line_no}: TOTAL for {rto} {month:%Y-%m} {fuel} is {expected}, "
                f"rows sum to {actual}"
            )
    return list(rows.values())
