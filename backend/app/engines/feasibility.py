"""Feasibility hard constraints (Section 9.5). Pure: measurements in, verdict out.

Every rule reports an outcome (pass | fail | not_applied | disabled |
deferred), the measured value and the threshold, so a rejection is always
explained by numbers rather than just a code. A site is feasible when no
rule fails.

- F01 inside water, forest, protected area, military land or an aerodrome.
- F02 no drivable road within the access distance.
- F03 (DC) an existing DC station within the minimum spacing, in a cell whose
  demand existing supply already meets (Phase 3 2SFCA access ratio).
- F04 too far from a grid asset. Only when grid data confidence is at least
  the configured minimum; otherwise recorded as not applied, and the
  distance becomes a score penalty in Phase 5 (Section 2 principle 5).
- F05 (DC) on a residential interior street: in residential land use or with
  only residential/unclassified road access, and no arterial road nearby.
- F06 hazard overlay: disabled until a flood-zone layer exists.
- F07 duplicate of a selected site: deferred to optimisation (Phase 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONFIDENCE_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
MINOR_ROAD_CLASSES = {"residential", "unclassified"}


@dataclass(frozen=True)
class Measurements:
    restricted_hits: list[dict[str, Any]]  # [{class, tag, name}]
    landuse: list[str]
    nearest_road_m: float | None
    nearest_road_class: str | None
    nearest_arterial_m: float | None
    nearest_dc_station_m: float | None
    cell_access_ratio: float | None
    nearest_substation_m: float | None
    grid_confidence: str


@dataclass(frozen=True)
class RuleConfig:
    enabled: dict[str, bool]
    max_road_distance_m: float
    min_dc_spacing_m: float
    served_access_ratio: float
    min_grid_confidence: str
    max_substation_distance_m: float
    max_arterial_distance_m: float


@dataclass
class Verdict:
    passed: bool
    reason_codes: list[str]
    details: dict[str, dict[str, Any]] = field(default_factory=dict)


def evaluate(m: Measurements, cfg: RuleConfig, dc: bool) -> Verdict:
    details: dict[str, dict[str, Any]] = {}

    def record(code: str, outcome: str, **info: Any) -> None:
        details[code] = {"outcome": outcome, **info}

    def active(code: str) -> bool:
        if not cfg.enabled.get(code, False):
            record(code, "disabled")
            return False
        return True

    if active("F01"):
        if m.restricted_hits:
            record("F01", "fail", measured=m.restricted_hits)
        else:
            record("F01", "pass")

    if active("F02"):
        if m.nearest_road_m is None or m.nearest_road_m > cfg.max_road_distance_m:
            record(
                "F02",
                "fail",
                measured=m.nearest_road_m,
                threshold=cfg.max_road_distance_m,
                unit="m",
            )
        else:
            record(
                "F02",
                "pass",
                measured=m.nearest_road_m,
                threshold=cfg.max_road_distance_m,
                unit="m",
            )

    if active("F03"):
        if not dc:
            record("F03", "not_applied", note="scenario includes no DC charger classes")
        elif (
            m.nearest_dc_station_m is not None
            and m.nearest_dc_station_m < cfg.min_dc_spacing_m
            and m.cell_access_ratio is not None
            and m.cell_access_ratio >= cfg.served_access_ratio
        ):
            record(
                "F03",
                "fail",
                measured=m.nearest_dc_station_m,
                threshold=cfg.min_dc_spacing_m,
                unit="m",
                access_ratio=m.cell_access_ratio,
                access_threshold=cfg.served_access_ratio,
            )
        else:
            record(
                "F03",
                "pass",
                measured=m.nearest_dc_station_m,
                threshold=cfg.min_dc_spacing_m,
                unit="m",
                access_ratio=m.cell_access_ratio,
            )

    if active("F04"):
        rank_ok = CONFIDENCE_RANK.get(m.grid_confidence, 0) >= CONFIDENCE_RANK.get(
            cfg.min_grid_confidence, 2
        )
        if not rank_ok:
            record(
                "F04",
                "not_applied",
                measured=m.nearest_substation_m,
                unit="m",
                note=(
                    f"grid data confidence {m.grid_confidence} is below "
                    f"{cfg.min_grid_confidence}; scored as a penalty instead of rejecting"
                ),
            )
        elif (
            m.nearest_substation_m is None or m.nearest_substation_m > cfg.max_substation_distance_m
        ):
            record(
                "F04",
                "fail",
                measured=m.nearest_substation_m,
                threshold=cfg.max_substation_distance_m,
                unit="m",
            )
        else:
            record(
                "F04",
                "pass",
                measured=m.nearest_substation_m,
                threshold=cfg.max_substation_distance_m,
                unit="m",
            )

    if active("F05"):
        interior = "residential" in m.landuse or m.nearest_road_class in MINOR_ROAD_CLASSES
        far_from_arterial = (
            m.nearest_arterial_m is None or m.nearest_arterial_m > cfg.max_arterial_distance_m
        )
        if not dc:
            record("F05", "not_applied", note="scenario includes no DC charger classes")
        elif interior and far_from_arterial:
            record(
                "F05",
                "fail",
                measured=m.nearest_arterial_m,
                threshold=cfg.max_arterial_distance_m,
                unit="m",
                landuse=m.landuse,
                nearest_road_class=m.nearest_road_class,
            )
        else:
            record(
                "F05",
                "pass",
                measured=m.nearest_arterial_m,
                threshold=cfg.max_arterial_distance_m,
                unit="m",
            )

    if active("F06"):
        record("F06", "not_applied", note="no hazard layer ingested")

    if active("F07"):
        record("F07", "deferred", note="evaluated during optimisation (Phase 6)")

    reasons = [code for code, d in details.items() if d["outcome"] == "fail"]
    return Verdict(passed=not reasons, reason_codes=reasons, details=details)
