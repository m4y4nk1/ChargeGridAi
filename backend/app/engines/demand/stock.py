"""Registrations -> vehicles in use (Section 9.2 step 1).

Stock at the end of month t is every vehicle registered up to t, discounted
by a constant annual scrappage rate r: a vehicle registered k months ago is
still in use with probability (1 - r) ** (k / 12). Equivalently,
stock[t] = stock[t-1] * (1 - r) ** (1/12) + registrations[t].

Vehicles registered before the first observed month are unknown and are
excluded — for EVs in India that pre-2021 base is small, and callers state
the observation start alongside every number.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def stock_from_registrations(
    monthly_registrations: NDArray[np.float64], annual_scrappage: float
) -> NDArray[np.float64]:
    """End-of-month stock for each month of a monthly registration series."""
    if not 0 <= annual_scrappage < 1:
        raise ValueError("annual scrappage rate must be in [0, 1)")
    monthly_survival = (1.0 - annual_scrappage) ** (1.0 / 12.0)
    regs = np.asarray(monthly_registrations, dtype=float)
    stock = np.empty_like(regs)
    running = 0.0
    for t, r in enumerate(regs):
        running = running * monthly_survival + r
        stock[t] = running
    return stock
