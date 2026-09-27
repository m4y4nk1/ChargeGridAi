from typing import Any

from pydantic import BaseModel


class IsochroneFeatureCollection(BaseModel):
    type: str = "FeatureCollection"
    features: list[dict[str, Any]]
