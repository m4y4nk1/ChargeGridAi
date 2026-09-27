"""Candidate generation helpers (Section 9.4). Pure; the DB-side sampling lives
in services/candidate_run.py.

- `pick_host`: for a point that should sit at a host (corridor samples, gap
  cells), choose the best nearby host by preference, then distance.
- `dedupe`: collapse candidates within `radius_m` of each other. The survivor
  is the one from the highest-priority origin (then hosted before roadside,
  then input order), and it records which other origins proposed that spot.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

ROADSIDE = "ROADSIDE"


@dataclass(frozen=True)
class HostOption:
    poi_id: str
    host_type: str
    name: str | None
    lat: float
    lng: float
    distance_m: float


@dataclass
class RawCandidate:
    origin: str  # poi | corridor | gap_cell | user
    lat: float
    lng: float
    host_type: str
    host_poi_id: str | None = None
    name: str | None = None
    detail: dict[str, object] = field(default_factory=dict)


@dataclass
class DedupedCandidate:
    candidate: RawCandidate
    merged_origins: list[str]
    merged_count: int


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def pick_host(
    options: Sequence[HostOption], preference: Sequence[str], within_m: float
) -> HostOption | None:
    eligible = [o for o in options if o.distance_m <= within_m]
    if not eligible:
        return None
    rank = {t: i for i, t in enumerate(preference)}
    return min(eligible, key=lambda o: (rank.get(o.host_type, len(rank)), o.distance_m, o.poi_id))


def dedupe(
    candidates: Sequence[RawCandidate], radius_m: float, origin_priority: Sequence[str]
) -> list[DedupedCandidate]:
    rank = {o: i for i, o in enumerate(origin_priority)}
    order = sorted(
        range(len(candidates)),
        key=lambda i: (
            rank.get(candidates[i].origin, len(rank)),
            candidates[i].host_type == ROADSIDE,
            i,
        ),
    )
    # Grid bucketing: cells at least radius_m wide, so matches are always within
    # the 3x3 neighbourhood of a candidate's own cell.
    cell_deg = radius_m / 111_000.0
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    kept: list[DedupedCandidate] = []

    def cell(c: RawCandidate) -> tuple[int, int]:
        lng_scale = max(math.cos(math.radians(c.lat)), 0.1)
        return int(c.lat // cell_deg), int(c.lng * lng_scale // cell_deg)

    for i in order:
        c = candidates[i]
        cy, cx = cell(c)
        match = None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for k in buckets.get((cy + dy, cx + dx), []):
                    other = kept[k].candidate
                    if haversine_m(c.lat, c.lng, other.lat, other.lng) <= radius_m:
                        match = k
                        break
                if match is not None:
                    break
            if match is not None:
                break
        if match is None:
            buckets[(cy, cx)].append(len(kept))
            kept.append(DedupedCandidate(c, [], 0))
        else:
            survivor = kept[match]
            survivor.merged_count += 1
            if c.origin != survivor.candidate.origin and c.origin not in survivor.merged_origins:
                survivor.merged_origins.append(c.origin)
    return kept
