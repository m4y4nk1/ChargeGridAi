"""Modelled regions (config/regions.yaml): bounding box, RTOs and default area.

Regions may overlap, so cell membership lives in `region_cell` and a region's
demand scenarios are namespaced: the default region keeps plain keys ("base",
"history") for continuity, others are prefixed ("mumbai_pune:base").
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core.assumptions import load_config


@dataclass(frozen=True)
class BBox:
    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float

    def contains(self, lat: float, lng: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lng <= lng <= self.max_lng

    @property
    def centre(self) -> tuple[float, float]:
        return (self.min_lat + self.max_lat) / 2, (self.min_lng + self.max_lng) / 2


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    bbox: BBox
    rtos: tuple[str, ...]
    default_area: int | None
    status: str = "onboarded"  # onboarded | planned

    @property
    def onboarded(self) -> bool:
        return self.status == "onboarded"


@lru_cache
def _registry() -> tuple[str, dict[str, Region]]:
    cfg = load_config("regions.yaml")
    regions = {
        rid: Region(
            id=rid,
            name=r["name"],
            bbox=BBox(**r["bbox"]),
            rtos=tuple(r["rtos"]),
            default_area=r.get("default_area"),
            status=r.get("status", "onboarded"),
        )
        for rid, r in cfg["regions"].items()
    }
    return str(cfg["default"]), regions


def default_region_id() -> str:
    return _registry()[0]


def get_region(region_id: str | None = None) -> Region:
    default, regions = _registry()
    rid = region_id or default
    if rid not in regions:
        raise KeyError(f"unknown region '{rid}'; configured: {sorted(regions)}")
    return regions[rid]


def all_regions(include_planned: bool = False) -> list[Region]:
    return [r for r in _registry()[1].values() if include_planned or r.onboarded]


def scenario_key(region_id: str, name: str) -> str:
    """Namespaced h3_cell_feature scenario: plain for the default region."""
    return name if region_id == default_region_id() else f"{region_id}:{name}"
