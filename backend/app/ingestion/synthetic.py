"""Clearly labelled synthetic fixtures (Section 0 rule 4).

Used only where real data isn't obtainable yet, so downstream engines can
be built and tested. Everything generated here is written under data source
`synthetic_vahan` (licence class SYNTHETIC, confidence NONE) and the UI
labels it as such. The numbers come from the formula below with a fixed
seed — they are NOT estimates of real Pune registrations.
"""

import csv
import math
import random
from datetime import date
from pathlib import Path

# RTO jurisdictions: the Pune Metropolitan Region core first, then the Mumbai-Pune
# corridor (Phase 9). The seeded stream runs in this order, so appending RTOs leaves
# the Pune series unchanged.
SYNTHETIC_RTOS = (
    "MH12",
    "MH14",  # Pune, Pimpri-Chinchwad
    "MH01",
    "MH02",
    "MH03",
    "MH47",  # Mumbai: Tardeo, Andheri, Worli, Borivali
    "MH04",
    "MH05",
    "MH43",
    "MH46",
    "MH06",  # Thane, Kalyan, Navi Mumbai, Panvel, Raigad (Pen)
)
# Relative size of each RTO's series: arbitrary fixture shape, NOT estimates.
_RTO_SCALE = {
    "MH12": 1.0,
    "MH14": 0.6,
    "MH01": 0.5,
    "MH02": 0.9,
    "MH03": 0.6,
    "MH47": 0.7,
    "MH04": 0.8,
    "MH05": 0.5,
    "MH43": 0.6,
    "MH46": 0.4,
    "MH06": 0.2,
}

# (vehicle_class, fuel, relative scale). Scales are arbitrary shape
# parameters for the fixture, not market-share estimates.
_CLASSES = (
    ("M-CYCLE/SCOOTER", "ELECTRIC(BOV)", 1.0),
    ("E-RICKSHAW(P)", "ELECTRIC(BOV)", 0.06),
    ("E-RICKSHAW WITH CART (G)", "ELECTRIC(BOV)", 0.03),
    ("THREE WHEELER (GOODS)", "ELECTRIC(BOV)", 0.04),
    ("MOTOR CAR", "ELECTRIC(BOV)", 0.12),
    ("MOTOR CAB", "ELECTRIC(BOV)", 0.03),
    ("BUS", "ELECTRIC(BOV)", 0.002),
    ("GOODS CARRIER", "ELECTRIC(BOV)", 0.01),
)


def generate_synthetic_vahan_csv(
    path: Path, start: date = date(2021, 1, 1), end: date = date(2026, 8, 1), seed: int = 20260924
) -> int:
    """Logistic-shaped monthly series per RTO x class, with seeded noise. Returns row count."""
    rng = random.Random(seed)
    months: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rto_code", "month", "fuel", "vehicle_class", "count"])
        for rto in SYNTHETIC_RTOS:
            rto_scale = _RTO_SCALE[rto]
            for t, month in enumerate(months):
                growth = 1 / (1 + math.exp(-(t - 40) / 10))
                for vehicle_class, fuel, class_scale in _CLASSES:
                    base = 3000 * rto_scale * class_scale * growth
                    count = max(0, round(base * rng.uniform(0.85, 1.15)))
                    writer.writerow([rto, f"{month:%Y-%m}", fuel, vehicle_class, count])
                    rows += 1
    return rows
