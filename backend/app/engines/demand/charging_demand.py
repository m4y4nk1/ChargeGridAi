"""Public charging energy demand (Section 9.2 step 3).

public kWh/day = vehicles x (annual km / 365) x kWh/km x public share x home-access modifier

The home-access modifier is 1 until a housing-type proxy (apartment
density, OSM building types) exists; the brief's formula is kept intact so
the modifier slots in without changing callers.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class SegmentUsage:
    annual_km: float
    kwh_per_km: float
    public_share: float
    kwh_per_public_session: float


def public_kwh_per_day(
    vehicles: NDArray[np.float64] | float,
    usage: SegmentUsage,
    home_access_modifier: NDArray[np.float64] | float = 1.0,
) -> NDArray[np.float64]:
    return np.asarray(
        np.asarray(vehicles, dtype=float)
        * (usage.annual_km / 365.0)
        * usage.kwh_per_km
        * usage.public_share
        * home_access_modifier
    )


def sessions_per_day(kwh: NDArray[np.float64], usage: SegmentUsage) -> NDArray[np.float64]:
    return np.asarray(kwh / usage.kwh_per_public_session)
