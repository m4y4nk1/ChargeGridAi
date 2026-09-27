"""Cross-source charging-station fusion (Section 7.3 of the project brief).

Pure and deterministic: takes every source's normalised station records and
returns fused stations, each listing the records it was built from, the
score that matched them, and any disagreements between sources.

1. Blocking: only records within a few H3 res-10 cells are compared.
2. Match score = distance (strong <= 75 m, weak <= 200 m) + name/operator
   trigram similarity (pg_trgm semantics) + power/connector agreement. Two
   known, clearly different operators never match — two CPOs can share a
   mall car park.
3. Records from the same source never merge with each other; a source's own
   listing of two nearby sites is taken at face value.
4. Merge: government > operator feed > OCM > OSM, field by field. EVSE lists
   are taken whole from the highest-priority source that has them, never
   summed across sources (that would double-count charge points).
5. Conflicts are recorded on the fused station, not hidden.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from itertools import combinations

import h3

from app.providers.base import EVSE, ChargingStation, LicenseClass

BLOCKING_RES = 10
# Res-10 hexagons are ~76 m edge; a 3-ring disk comfortably covers 200 m.
BLOCKING_K = 3
STRONG_MATCH_M = 75.0
WEAK_MATCH_M = 200.0
MATCH_THRESHOLD = 0.6
OPERATOR_VETO_SIMILARITY = 0.3
POWER_TOLERANCE = 0.25

_STATION_NAMESPACE = uuid.UUID("5b0c7a62-1f7e-4c6e-9d0b-3e0b8a1c2f10")

_LICENSE_RESTRICTIVENESS = [
    LicenseClass.OPEN,
    LicenseClass.GOVERNMENT,
    LicenseClass.SYNTHETIC,
    LicenseClass.RESTRICTED_GOOGLE,
    LicenseClass.RESTRICTED_COMMERCIAL,
]

_MERGED_FIELDS = ("name", "operator", "address", "host_type", "access", "status", "opening_hours")


def source_priority(record: ChargingStation) -> int:
    if record.license_class == LicenseClass.GOVERNMENT:
        return 0
    if record.source.startswith("operator:"):
        return 1
    if record.source == "ocm":
        return 2
    if record.source == "osm":
        return 3
    return 4


def trigram_similarity(a: str | None, b: str | None) -> float | None:
    """pg_trgm-compatible similarity; None if either side is missing."""
    if not a or not b:
        return None
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return None
    return len(ta & tb) / len(ta | tb)


def _trigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        padded = f"  {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


def haversine_m(a: ChargingStation, b: ChargingStation) -> float:
    lat1, lng1 = math.radians(a.location.lat), math.radians(a.location.lng)
    lat2, lng2 = math.radians(b.location.lat), math.radians(b.location.lng)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _distance_score(d: float) -> float:
    if d <= STRONG_MATCH_M:
        return 1.0
    if d <= WEAK_MATCH_M:
        return 0.8 - 0.5 * (d - STRONG_MATCH_M) / (WEAK_MATCH_M - STRONG_MATCH_M)
    return 0.0


def _max_kw(record: ChargingStation) -> float | None:
    values = [e.max_kw for e in record.evses if e.max_kw is not None]
    return max(values) if values else None


def _standards(record: ChargingStation) -> set[str]:
    return {c.standard for e in record.evses for c in e.connectors if c.standard != "UNKNOWN"}


def _power_score(a: ChargingStation, b: ChargingStation) -> float:
    kw_a, kw_b = _max_kw(a), _max_kw(b)
    std_a, std_b = _standards(a), _standards(b)
    parts: list[float] = []
    if kw_a is not None and kw_b is not None:
        parts.append(1.0 if abs(kw_a - kw_b) <= POWER_TOLERANCE * max(kw_a, kw_b) else 0.0)
    if std_a and std_b:
        parts.append(len(std_a & std_b) / len(std_a | std_b))
    return sum(parts) / len(parts) if parts else 0.5


def match_score(a: ChargingStation, b: ChargingStation) -> float | None:
    """Score in [0, 1], or None when the pair can't be the same station."""
    if a.source == b.source:
        return None
    d = haversine_m(a, b)
    if d > WEAK_MATCH_M:
        return None
    operator_sim = trigram_similarity(a.operator, b.operator)
    if operator_sim is not None and operator_sim < OPERATOR_VETO_SIMILARITY:
        return None
    name_sims = [s for s in (trigram_similarity(a.name, b.name), operator_sim) if s is not None]
    name_score = max(name_sims) if name_sims else 0.5
    return 0.6 * _distance_score(d) + 0.25 * name_score + 0.15 * _power_score(a, b)


@dataclass
class FusedStation:
    id: uuid.UUID
    location: tuple[float, float]  # (lat, lng)
    fields: dict[str, str | None]
    evses: list[EVSE]
    license_class: LicenseClass
    confidence: str
    members: list[tuple[ChargingStation, float]]
    conflicts: list[dict[str, object]] = field(default_factory=list)


def fuse(records: list[ChargingStation]) -> list[FusedStation]:
    for r in records:
        if r.license_class == LicenseClass.RESTRICTED_GOOGLE:
            # Google only contributes a place_id link at display time (Section 7.3.3).
            raise ValueError(f"Google-sourced record {r.id} must not enter the fused store")

    ordered = sorted(records, key=lambda r: (source_priority(r), r.source, r.id))
    pairs = sorted(_candidate_pairs(ordered), key=lambda p: (-p[0], p[1], p[2]))

    parent = list(range(len(ordered)))
    sources: list[set[str]] = [{r.source} for r in ordered]
    joined_by: dict[int, float] = {}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for score, i, j in pairs:
        ri, rj = find(i), find(j)
        if ri == rj or sources[ri] & sources[rj]:
            continue
        keep, drop = (ri, rj) if ri < rj else (rj, ri)
        parent[drop] = keep
        sources[keep] |= sources[drop]
        # Pairs arrive best-first, so the first link recorded is each record's strongest.
        joined_by.setdefault(i, score)
        joined_by.setdefault(j, score)

    clusters: dict[int, list[int]] = {}
    for i in range(len(ordered)):
        clusters.setdefault(find(i), []).append(i)

    return [
        _merge([ordered[i] for i in members], [joined_by.get(i, 1.0) for i in members])
        for members in clusters.values()
    ]


def _candidate_pairs(records: list[ChargingStation]) -> list[tuple[float, int, int]]:
    cells: dict[str, list[int]] = {}
    for idx, r in enumerate(records):
        cells.setdefault(
            h3.latlng_to_cell(r.location.lat, r.location.lng, BLOCKING_RES), []
        ).append(idx)

    seen: set[tuple[int, int]] = set()
    pairs: list[tuple[float, int, int]] = []
    for idx, r in enumerate(records):
        home = h3.latlng_to_cell(r.location.lat, r.location.lng, BLOCKING_RES)
        for cell in h3.grid_disk(home, BLOCKING_K):
            for other in cells.get(cell, []):
                if other <= idx or (idx, other) in seen:
                    continue
                seen.add((idx, other))
                score = match_score(r, records[other])
                if score is not None and score >= MATCH_THRESHOLD:
                    pairs.append((score, idx, other))
    return pairs


def _merge(members: list[ChargingStation], scores: list[float]) -> FusedStation:
    ranked = sorted(
        zip(members, scores, strict=True), key=lambda m: (source_priority(m[0]), m[0].id)
    )
    anchor = ranked[0][0]

    fields = {
        name: next((getattr(m, name) for m, _ in ranked if getattr(m, name)), None)
        for name in _MERGED_FIELDS
    }
    evses = next((m.evses for m, _ in ranked if m.evses), [])
    license_class = max((m.license_class for m, _ in ranked), key=_LICENSE_RESTRICTIVENESS.index)

    distinct_sources = {m.source for m, _ in ranked}
    if any(m.confidence == "LOW" for m, _ in ranked) and len(distinct_sources) == 1:
        confidence = "LOW"
    elif anchor.license_class == LicenseClass.GOVERNMENT or len(distinct_sources) > 1:
        confidence = "HIGH"
    else:
        confidence = "MEDIUM"

    return FusedStation(
        id=uuid.uuid5(_STATION_NAMESPACE, f"{anchor.source}:{anchor.id}"),
        location=(anchor.location.lat, anchor.location.lng),
        fields=fields,
        evses=evses,
        license_class=license_class,
        confidence=confidence,
        members=[(m, 1.0 if m is anchor else s) for m, s in ranked],
        conflicts=_conflicts([m for m, _ in ranked]),
    )


def _conflicts(members: list[ChargingStation]) -> list[dict[str, object]]:
    conflicts: list[dict[str, object]] = []
    if len(members) < 2:
        return conflicts

    counts = {m.source: sum(e.count for e in m.evses) for m in members if m.evses}
    if len(set(counts.values())) > 1:
        conflicts.append({"field": "charge_point_count", "values": counts})

    kws = {m.source: kw for m in members if (kw := _max_kw(m)) is not None}
    if kws and max(kws.values()) - min(kws.values()) > POWER_TOLERANCE * max(kws.values()):
        conflicts.append({"field": "max_kw", "values": kws})

    operators = {m.source: m.operator for m in members if m.operator}
    if len(operators) > 1:
        names = list(operators.values())
        if any((trigram_similarity(x, y) or 0.0) < 0.6 for x, y in combinations(names, 2)):
            conflicts.append({"field": "operator", "values": operators})
    return conflicts
