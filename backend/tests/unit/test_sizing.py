import math

import numpy as np
import pytest

from app.engines.sizing.queueing import (
    SegmentDemand,
    allocate_guns,
    choose_chargers,
    connector_mix,
    erlang_c,
    hourly_profile,
    mean_wait,
    prob_wait_longer_than,
    session_minutes,
    simulate,
)


def test_erlang_c_known_values() -> None:
    # M/M/2 at 1 Erlang: P0 = 1/3, C = (1/2!)(2/(2-1)) P0 = 1/3.
    assert erlang_c(2, 1.0) == pytest.approx(1 / 3)
    # M/M/1: C = rho.
    assert erlang_c(1, 0.6) == pytest.approx(0.6)
    assert erlang_c(3, 3.0) == 1.0  # unstable: everyone waits
    assert erlang_c(4, 0.0) == 0.0
    # Stable for large systems (the naive factorial form overflows).
    assert 0 < erlang_c(200, 180.0) < 1


def test_wait_formulas() -> None:
    lam, mu = 0.5, 0.1  # per minute: 30 arrivals/h, 10-min service, a = 5
    c = 7
    assert mean_wait(c, lam, mu) == pytest.approx(erlang_c(c, 5.0) / (c * mu - lam))
    p0 = prob_wait_longer_than(c, lam, mu, 0.0)
    assert p0 == pytest.approx(erlang_c(c, 5.0))
    assert prob_wait_longer_than(c, lam, mu, 10.0) < p0
    assert mean_wait(5, lam, mu) == math.inf


def test_choose_chargers_meets_targets_and_grows_with_demand() -> None:
    small = choose_chargers(6, 30, 10, 0.1, 0.85)
    big = choose_chargers(30, 30, 10, 0.1, 0.85)
    assert small.chargers < big.chargers
    for ch in (small, big):
        assert ch.prob_wait_over <= 0.1 and ch.peak_utilisation <= 0.85 and not ch.capped
    # One fewer charger would miss a target.
    lam, mu = 30 / 60, 1 / 30
    worse = prob_wait_longer_than(big.chargers - 1, lam, mu, 10)
    assert worse > 0.1 or lam / ((big.chargers - 1) * mu) > 0.85
    assert choose_chargers(1000, 60, 10, 0.1, 0.85, max_chargers=5).capped


def test_session_minutes_respects_vehicle_limit() -> None:
    # A 50 kW car on a 120 kW charger (taper 0.75 -> 90 kW) charges at 50 kW.
    assert session_minutes(25, 120, 0.75, 50, 5) == pytest.approx(60 * 25 / 50 + 5)
    assert session_minutes(25, 60, 0.85, 50, 5) == pytest.approx(60 * 25 / 50 + 5)
    assert session_minutes(25, 30, 0.9, 50, 5) == pytest.approx(60 * 25 / 27 + 5)


def test_hourly_profile_conserves_sessions() -> None:
    segs = [
        SegmentDemand("a", 200, 20, 50, [1] * 24),
        SegmentDemand("b", 100, 25, 50, [0] * 12 + [2] * 12),
    ]
    hourly, sessions = hourly_profile(segs, [2] * 24)
    assert sessions.tolist() == [10, 4]
    assert hourly.sum() == pytest.approx(14)
    assert hourly[:12].sum() == pytest.approx(5)  # only segment a before noon


def test_simulation_matches_erlang_for_a_stationary_mmc_queue() -> None:
    # 24 arrivals/h, 12-min exponential service -> a = 4.8 Erlangs on 7 chargers.
    sim = simulate(
        [24] * 24, [1.0], [12.0], chargers=7, days=150, service_cv=1.0, max_wait_minutes=5, seed=1
    )
    lam, mu = 24 / 60, 1 / 12
    assert sim.prob_wait_over == pytest.approx(prob_wait_longer_than(7, lam, mu, 5), abs=0.02)
    assert sim.utilisation == pytest.approx(4.8 / 7, abs=0.02)
    assert sim.mean_wait_min == pytest.approx(mean_wait(7, lam, mu), rel=0.25)
    assert len(sim.hourly_utilisation) == 24


def test_simulation_is_seeded() -> None:
    a = simulate([5] * 24, [0.5, 0.5], [20, 40], 3, 20, 0.5, 10, seed=7)
    b = simulate([5] * 24, [0.5, 0.5], [20, 40], 3, 20, 0.5, 10, seed=7)
    assert a == b


def test_connector_mix_and_gun_allocation() -> None:
    mix = connector_mix(
        {"car": 30, "fleet": 10},
        {"car": {"CCS2": 1.0}, "fleet": {"CCS2": 0.8, "BHARAT_DC001": 0.2}},
    )
    assert mix == pytest.approx({"CCS2": 0.95, "BHARAT_DC001": 0.05})
    assert list(mix) == ["CCS2", "BHARAT_DC001"]
    guns = allocate_guns(mix, 8)
    assert sum(guns.values()) == 8 and guns["CCS2"] >= 7
    assert allocate_guns({"CCS2": 0.4, "X": 0.6}, 1) == {"X": 1}
    assert allocate_guns(mix, 0) == {}
    assert np.isclose(sum(connector_mix({"a": 1}, {"a": {"T": 1.0}}).values()), 1.0)
