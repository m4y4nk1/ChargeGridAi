"""24-hour charging load profiles (Section 9.11, module E).

A site's load at the meter is its daily served energy spread over the hours by the
simulated occupancy from sizing (Phase 7), divided by charger efficiency. Hourly
values are average kW over the hour (= kWh in that hour).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

HOURS = 24


def site_hourly_kw(
    served_kwh_per_day: float, hourly_utilisation: Sequence[float], efficiency: float
) -> list[float]:
    if len(hourly_utilisation) != HOURS:
        raise ValueError("hourly_utilisation needs 24 values")
    if not 0 < efficiency <= 1:
        raise ValueError("efficiency must be in (0, 1]")
    total = sum(hourly_utilisation) or 1.0
    return [served_kwh_per_day * u / total / efficiency for u in hourly_utilisation]


def split_by_share(hourly: Sequence[float], shares: dict[str, float]) -> dict[str, list[float]]:
    """Split a profile by energy shares (e.g. vehicle segments); shares are normalised."""
    total = sum(shares.values())
    if total <= 0:
        return {}
    return {k: [h * v / total for h in hourly] for k, v in shares.items() if v > 0}


def stack(profiles: Iterable[tuple[str, Sequence[float]]]) -> dict[str, list[float]]:
    """Sum profiles by key (e.g. charger class)."""
    out: dict[str, list[float]] = {}
    for key, hourly in profiles:
        acc = out.setdefault(key, [0.0] * HOURS)
        for h in range(HOURS):
            acc[h] += hourly[h]
    return out


def total(profiles: dict[str, list[float]]) -> list[float]:
    return [sum(p[h] for p in profiles.values()) for h in range(HOURS)]
