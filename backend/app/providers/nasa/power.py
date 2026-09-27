"""NASA POWER hourly point data (https://power.larc.nasa.gov), no key needed.

Fetches a year of hourly global horizontal irradiance (ALLSKY_SFC_SW_DWN, W/m2)
and 2 m air temperature (T2M, degC) in local solar time, and reduces it to
representative days: the mean profile of each hour of the day, for each month.
The solar resource varies little across the Pune region (tens of km), so one
point at the region centre stands for every site.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

import httpx

URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
PARAMETERS = ("ALLSKY_SFC_SW_DWN", "T2M")


@dataclass(frozen=True)
class RepresentativeHour:
    month: int  # 1-12
    hour: int  # 0-23, local solar time
    ghi_wm2: float
    temp_c: float
    days: int  # days in that month with valid data


async def fetch_year(lat: float, lng: float, year: int) -> bytes:
    params: dict[str, str | float] = {
        "parameters": ",".join(PARAMETERS),
        "community": "RE",
        "latitude": lat,
        "longitude": lng,
        "start": f"{year}0101",
        "end": f"{year}1231",
        "format": "JSON",
        "time-standard": "LST",
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.get(URL, params=params)
        response.raise_for_status()
        return response.content


def representative_days(raw: bytes) -> tuple[list[RepresentativeHour], dict[str, int]]:
    """Mean diurnal profile per month; fill values (-999) are skipped and counted."""
    data = json.loads(raw)
    fill = float(data["header"].get("fill_value", -999.0))
    params = data["properties"]["parameter"]
    ghi, temp = params["ALLSKY_SFC_SW_DWN"], params["T2M"]
    sums: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    days: dict[int, set[int]] = defaultdict(set)
    missing = 0
    for stamp, g in ghi.items():  # YYYYMMDDHH
        t = temp.get(stamp, fill)
        if g == fill or t == fill:
            missing += 1
            continue
        month, day, hour = int(stamp[4:6]), int(stamp[6:8]), int(stamp[8:10])
        acc = sums[(month, hour)]
        acc[0] += float(g)
        acc[1] += float(t)
        acc[2] += 1
        days[month].add(day)
    rows = [
        RepresentativeHour(m, h, acc[0] / acc[2], acc[1] / acc[2], len(days[m]))
        for (m, h), acc in sorted(sums.items())
    ]
    return rows, {"hours": len(ghi), "missing_hours": missing}
