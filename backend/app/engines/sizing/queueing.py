"""Charger sizing (Section 9.8). Pure: a site's demand in, a charger count and service
metrics out.

1. Sessions per day from the kWh the site serves and each segment's kWh per session.
2. An hourly arrival profile from segment 24-hour curves times a host-type modifier.
3. Session duration = kWh / effective power (charger kW x taper, capped by what the
   vehicle accepts) + plug-in overhead.
4. For the peak hour, the smallest charger count meeting the Erlang-C targets:
   P(wait > t) <= target and peak occupancy <= a ceiling.
5. Validation of that count with a SimPy simulation over many days (non-homogeneous
   Poisson arrivals, gamma-distributed service), which Erlang-C can't capture:
   queues carrying over between hours and less variable sessions.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import simpy

# --- Erlang-C ----------------------------------------------------------------------------


def erlang_c(servers: int, offered_load: float) -> float:
    """Probability an arrival waits (M/M/c), offered load a = lambda / mu in Erlangs.

    Uses the Erlang-B recursion, stable for large c: B(k) = a B(k-1) / (k + a B(k-1)),
    then C = c B / (c - a (1 - B)).
    """
    if offered_load <= 0:
        return 0.0
    if servers <= offered_load:
        return 1.0
    b = 1.0
    for k in range(1, servers + 1):
        b = offered_load * b / (k + offered_load * b)
    return servers * b / (servers - offered_load * (1 - b))


def prob_wait_longer_than(
    servers: int, arrival_rate: float, service_rate: float, t: float
) -> float:
    """P(W > t) for M/M/c; rates per the same time unit as t."""
    a = arrival_rate / service_rate
    if servers <= a:
        return 1.0
    return erlang_c(servers, a) * math.exp(-(servers * service_rate - arrival_rate) * t)


def mean_wait(servers: int, arrival_rate: float, service_rate: float) -> float:
    a = arrival_rate / service_rate
    if servers <= a:
        return math.inf
    return erlang_c(servers, a) / (servers * service_rate - arrival_rate)


@dataclass(frozen=True)
class ErlangChoice:
    chargers: int
    prob_wait_over: float
    mean_wait_min: float
    peak_utilisation: float
    capped: bool  # hit max_chargers without meeting the targets


def choose_chargers(
    peak_arrivals_per_hour: float,
    service_minutes: float,
    max_wait_minutes: float,
    max_prob_wait: float,
    max_peak_utilisation: float,
    min_chargers: int = 1,
    max_chargers: int = 60,
) -> ErlangChoice:
    """Smallest c meeting P(wait > t) <= target and peak occupancy <= the ceiling."""
    lam = peak_arrivals_per_hour / 60.0  # per minute
    mu = 1.0 / service_minutes
    for c in range(max(min_chargers, 1), max_chargers + 1):
        rho = lam / (c * mu)
        p = prob_wait_longer_than(c, lam, mu, max_wait_minutes)
        if p <= max_prob_wait and rho <= max_peak_utilisation:
            return ErlangChoice(c, p, mean_wait(c, lam, mu), rho, False)
    c = max_chargers
    return ErlangChoice(
        c,
        prob_wait_longer_than(c, lam, mu, max_wait_minutes),
        mean_wait(c, lam, mu),
        lam / (c * mu),
        True,
    )


# --- demand -> arrivals -------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentDemand:
    name: str
    kwh_per_day: float
    kwh_per_session: float
    max_kw: float
    curve: Sequence[float]  # 24 relative weights


def session_minutes(
    kwh: float, charger_kw: float, taper: float, vehicle_max_kw: float, overhead_min: float
) -> float:
    effective_kw = min(charger_kw * taper, vehicle_max_kw)
    return 60.0 * kwh / effective_kw + overhead_min


def hourly_profile(
    segments: Sequence[SegmentDemand], host_modifier: Sequence[float] | None
) -> tuple[np.ndarray, np.ndarray]:
    """(arrivals per hour of day [24], sessions per day by segment)."""
    sessions = np.array([s.kwh_per_day / s.kwh_per_session for s in segments])
    hourly = np.zeros(24)
    mod = np.asarray(host_modifier, dtype=float) if host_modifier is not None else np.ones(24)
    for s, n in zip(segments, sessions, strict=True):
        shape = np.asarray(s.curve, dtype=float) * mod
        hourly += n * shape / shape.sum()
    return hourly, sessions


# --- SimPy validation ---------------------------------------------------------------------


@dataclass(frozen=True)
class SimResult:
    days: int
    sessions: int
    prob_wait_over: float  # all arrivals
    prob_wait_over_peak_hour: float
    mean_wait_min: float
    p95_wait_min: float
    utilisation: float  # busy charger-time / available charger-time
    hourly_utilisation: list[float]
    hourly_prob_wait_over: list[float]


def simulate(
    hourly_arrivals: Sequence[float],
    segment_share: Sequence[float],
    segment_service_min: Sequence[float],
    chargers: int,
    days: int,
    service_cv: float,
    max_wait_minutes: float,
    seed: int,
) -> SimResult:
    """Continuous-time M(t)/G/c FIFO queue over `days`, in minutes."""
    rng = np.random.default_rng(seed)
    rates = np.asarray(hourly_arrivals, dtype=float)
    # Arrival times: Poisson counts per hour, uniform within the hour.
    counts = rng.poisson(np.tile(rates, days))
    hours = np.repeat(np.arange(24 * days), counts)
    times = np.sort((hours + rng.random(hours.size)) * 60.0)
    seg = rng.choice(
        len(segment_share), size=times.size, p=np.asarray(segment_share) / np.sum(segment_share)
    )
    mean = np.asarray(segment_service_min, dtype=float)[seg]
    shape = 1.0 / service_cv**2
    service = rng.gamma(shape, mean / shape)

    env = simpy.Environment()
    pool = simpy.Resource(env, capacity=chargers)
    waits = np.zeros(times.size)
    busy = np.zeros(24)

    def session(i: int) -> simpy.events.ProcessGenerator:
        with pool.request() as req:
            yield req
            waits[i] = env.now - times[i]
            start = env.now
            yield env.timeout(service[i])
            _book(busy, start, env.now)

    def source() -> simpy.events.ProcessGenerator:
        for i, t in enumerate(times):
            yield env.timeout(t - env.now)
            env.process(session(i))

    env.process(source())
    env.run()

    peak = int(np.argmax(rates))
    arr_hour = (times // 60).astype(int) % 24
    over = waits > max_wait_minutes
    hourly_p = [
        float(over[arr_hour == h].mean()) if np.any(arr_hour == h) else 0.0 for h in range(24)
    ]
    return SimResult(
        days=days,
        sessions=int(times.size),
        prob_wait_over=float(over.mean()) if times.size else 0.0,
        prob_wait_over_peak_hour=hourly_p[peak],
        mean_wait_min=float(waits.mean()) if times.size else 0.0,
        p95_wait_min=float(np.percentile(waits, 95)) if times.size else 0.0,
        utilisation=float(busy.sum() / (chargers * 24 * 60 * days)),
        hourly_utilisation=[float(b / (chargers * 60 * days)) for b in busy],
        hourly_prob_wait_over=hourly_p,
    )


def _book(busy: np.ndarray, start: float, end: float) -> None:
    """Add a busy interval (minutes since t=0) to per-hour-of-day busy minutes."""
    t = start
    while t < end:
        hour_end = (math.floor(t / 60) + 1) * 60
        seg_end = min(end, hour_end)
        busy[int(t // 60) % 24] += seg_end - t
        t = seg_end


# --- connectors ---------------------------------------------------------------------------


def connector_mix(
    sessions_by_segment: Mapping[str, float], connectors: Mapping[str, Mapping[str, float]]
) -> dict[str, float]:
    """Share of sessions needing each connector standard."""
    total: dict[str, float] = {}
    for seg, n in sessions_by_segment.items():
        for std, share in connectors[seg].items():
            total[std] = total.get(std, 0.0) + n * share
    s = sum(total.values())
    return {k: v / s for k, v in sorted(total.items(), key=lambda kv: -kv[1])} if s else {}


def allocate_guns(shares: Mapping[str, float], guns: int) -> dict[str, int]:
    """Largest-remainder split of `guns` over connector shares; the leading standard
    always gets at least one gun."""
    if guns <= 0 or not shares:
        return {}
    raw = {k: v * guns for k, v in shares.items()}
    out = {k: int(math.floor(v)) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: guns - sum(out.values())]:
        out[k] += 1
    lead = max(shares, key=lambda k: shares[k])
    if out[lead] == 0:
        donor = max(out, key=lambda k: out[k])
        out[donor] -= 1
        out[lead] = 1
    return {k: v for k, v in out.items() if v > 0}
