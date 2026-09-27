"""Bass-diffusion adoption forecast with bootstrap bands (Section 9.2 step 2).

Cumulative adopters N(t) = m * F(t), with
    F(t) = (1 - e^{-(p+q)t}) / (1 + (q/p) e^{-(p+q)t}),
t in years from the first observed month. (m, p, q) are fitted by least
squares to monthly registrations. The market potential m is bounded between
just above what's already registered and `max_potential_multiple` times
that, because early-curve data can't pin m down on its own.

Uncertainty: relative residuals of the fit are resampled onto the fitted
curve, the model is refitted to each resampled series, and every refit is
projected forward. P10/P50/P90 are taken across those paths *after*
converting registrations to vehicles in use, so the bands are on stock.

Stock: history is observed registrations discounted by scrappage. In the
forecast, Bass projects new adopters and scrapped EVs are assumed replaced by
EVs (no reversion to ICE), so stock grows by new adopters and never falls.
Feeding Bass adoptions into a scrappage-only stock sum would wrongly shrink
the fleet once adoption saturates.

Scenario cases and the what-if multiplier scale only new adopters after the
last observed month; history is never altered.

Series too thin to fit (fewer than `min_total` registrations) fall back to a
flat projection at the trailing 12-month mean, flagged as such.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares

from app.engines.demand.stock import stock_from_registrations

MIN_TOTAL_REGISTRATIONS = 30.0


@dataclass(frozen=True)
class BassParams:
    m: float
    p: float
    q: float


@dataclass(frozen=True)
class BassFit:
    params: BassParams | None  # None when the flat fallback was used
    method: str
    rmse: float
    fitted: NDArray[np.float64]  # fitted monthly registrations over the observed window


@dataclass(frozen=True)
class StockForecast:
    """End-of-month stock quantiles over the whole window (observed + projected)."""

    method: str
    params: BassParams | None
    rmse: float
    p10: NDArray[np.float64]
    p50: NDArray[np.float64]
    p90: NDArray[np.float64]
    registrations_p50: NDArray[np.float64]


def bass_cumulative(t_years: NDArray[np.float64], params: BassParams) -> NDArray[np.float64]:
    pq = params.p + params.q
    e = np.exp(-pq * t_years)
    return params.m * (1.0 - e) / (1.0 + (params.q / params.p) * e)


def bass_monthly(n_months: int, params: BassParams) -> NDArray[np.float64]:
    """Registrations in each month 0..n_months-1 (month k ends at t = (k+1)/12)."""
    t = np.arange(n_months + 1, dtype=float) / 12.0
    return np.diff(bass_cumulative(t, params))


def fit_bass(monthly: NDArray[np.float64], max_potential_multiple: float = 100.0) -> BassFit:
    obs = np.asarray(monthly, dtype=float)
    total = float(obs.sum())
    if total < MIN_TOTAL_REGISTRATIONS:
        level = float(obs[-12:].mean()) if len(obs) else 0.0
        fitted = np.full_like(obs, level)
        return BassFit(
            None, "flat_trailing_mean", float(np.sqrt(np.mean((obs - fitted) ** 2))), fitted
        )

    n = len(obs)

    def residuals(x: NDArray[np.float64]) -> NDArray[np.float64]:
        params = BassParams(m=x[0] * total, p=x[1], q=x[2])
        scale = max(float(obs.max()), 1.0)
        return np.asarray((bass_monthly(n, params) - obs) / scale, dtype=float)

    best = None
    for q0 in (0.3, 0.8, 1.5):  # a few starts: the objective has shallow valleys
        result = least_squares(
            residuals,
            x0=np.array([3.0, 0.01, q0]),
            bounds=([1.05, 1e-5, 0.0], [max_potential_multiple, 0.2, 3.0]),
        )
        if best is None or result.cost < best.cost:
            best = result
    assert best is not None
    params = BassParams(m=float(best.x[0] * total), p=float(best.x[1]), q=float(best.x[2]))
    fitted = bass_monthly(n, params)
    return BassFit(params, "bass_v1", float(np.sqrt(np.mean((obs - fitted) ** 2))), fitted)


def project_registrations(
    fit: BassFit, n_observed: int, horizon_months: int
) -> NDArray[np.float64]:
    """Fitted-model registrations for months n_observed .. horizon_months-1."""
    if fit.params is None:
        return np.full(
            horizon_months - n_observed, float(fit.fitted[-1]) if len(fit.fitted) else 0.0
        )
    return bass_monthly(horizon_months, fit.params)[n_observed:]


@dataclass(frozen=True)
class ForecastPaths:
    """Observed registrations plus bootstrap draws of future registrations (unscaled)."""

    observed: NDArray[np.float64]
    future: NDArray[np.float64]  # shape (draws, horizon - observed); row 0 is the point fit
    method: str
    params: BassParams | None
    rmse: float


def forecast_paths(
    monthly: NDArray[np.float64],
    horizon_months: int,
    n_bootstrap: int = 200,
    seed: int = 0,
    max_potential_multiple: float = 100.0,
) -> ForecastPaths:
    obs = np.asarray(monthly, dtype=float)
    n = len(obs)
    if horizon_months < n:
        raise ValueError("horizon must not end before the observed data")
    base = fit_bass(obs, max_potential_multiple)

    rng = np.random.default_rng(seed)
    safe_fitted = np.where(base.fitted > 0, base.fitted, np.nan)
    rel = np.nan_to_num(obs / safe_fitted - 1.0, nan=0.0)
    futures = [project_registrations(base, n, horizon_months)]
    if base.params is not None:
        for _ in range(n_bootstrap):
            resampled = np.clip(base.fitted * (1.0 + rng.choice(rel, size=n)), 0.0, None)
            futures.append(
                project_registrations(
                    fit_bass(resampled, max_potential_multiple), n, horizon_months
                )
            )
    return ForecastPaths(
        observed=obs,
        future=np.clip(np.array(futures), 0.0, None),
        method=base.method,
        params=base.params,
        rmse=base.rmse,
    )


def stock_quantiles(
    paths: ForecastPaths, annual_scrappage: float, multiplier: float = 1.0
) -> StockForecast:
    """P10/P50/P90 of vehicles in use, observed history then forecast.

    History: every observed registration, discounted by scrappage.
    Forecast: Bass projects *new adopters*; scrapped EVs are assumed to be
    replaced by EVs, so stock grows by `multiplier` x new adopters and never
    falls. Forecast registrations = new adopters + those replacements.
    """
    observed_stock = stock_from_registrations(paths.observed, annual_scrappage)
    last = float(observed_stock[-1]) if len(observed_stock) else 0.0
    retire_share = 1.0 - (1.0 - annual_scrappage) ** (1.0 / 12.0)

    stocks, regs = [], []
    for future in paths.future:
        adopters = multiplier * future
        future_stock = last + np.cumsum(adopters)
        previous = np.concatenate([[last], future_stock[:-1]])
        stocks.append(np.concatenate([observed_stock, future_stock]))
        regs.append(np.concatenate([paths.observed, adopters + previous * retire_share]))
    stock_arr, reg_arr = np.array(stocks), np.array(regs)
    return StockForecast(
        method=paths.method,
        params=paths.params,
        rmse=paths.rmse,
        p10=np.percentile(stock_arr, 10, axis=0),
        p50=np.percentile(stock_arr, 50, axis=0),
        p90=np.percentile(stock_arr, 90, axis=0),
        registrations_p50=np.percentile(reg_arr, 50, axis=0),
    )


def forecast_stock(
    monthly: NDArray[np.float64],
    horizon_months: int,
    annual_scrappage: float,
    multiplier: float = 1.0,
    n_bootstrap: int = 200,
    seed: int = 0,
    max_potential_multiple: float = 100.0,
) -> StockForecast:
    paths = forecast_paths(monthly, horizon_months, n_bootstrap, seed, max_potential_multiple)
    return stock_quantiles(paths, annual_scrappage, multiplier)


def cagr_baseline(monthly: NDArray[np.float64], horizon_months: int) -> NDArray[np.float64]:
    """Naive comparator: trailing-12-month level grown at the last year-on-year rate."""
    obs = np.asarray(monthly, dtype=float)
    n = len(obs)
    last, prev = obs[-12:].sum(), obs[-24:-12].sum()
    growth = (last / prev) if prev > 0 else 1.0
    monthly_growth = growth ** (1.0 / 12.0)
    level = last / 12.0
    future = level * monthly_growth ** np.arange(1, horizon_months - n + 1, dtype=float)
    return np.concatenate([obs, future])
