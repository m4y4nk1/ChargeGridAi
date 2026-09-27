from typing import Any

from pydantic import BaseModel


class RegionSummary(BaseModel):
    id: int
    name: str | None
    level: str
    admin_level: int | None
    code: str | None


class Region(RegionSummary):
    geometry: dict[str, Any]
