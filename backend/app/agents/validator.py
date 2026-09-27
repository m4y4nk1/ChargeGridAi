"""Numeric-claim validator (Section 10.3).

Every number, percentage and ₹ amount in a draft must match, within rounding, a value
some tool returned in this session (or the user's own request). A draft that fails is
regenerated once with the offending numbers listed; what still fails is removed
sentence by sentence and flagged.

Matching: "₹30.4 crore" is 30.4 x 1e7 INR shown to one decimal, so it matches any fact
within 0.05 crore (or 0.5% relative, whichever is looser). Units that are commonly
rescaled in prose are tried at each scale (MW vs kW, km vs m, minutes vs seconds,
percent vs fraction). Signs are ignored ("a loss of ₹1.2 crore" states -1.2e7).
Not claims: four-digit years in the configured range, identifiers ("DC_120", "MH12",
"P50", "NH48"), ordinals, list markers, and anything inside code spans or URLs.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

_NUMBER = re.compile(
    r"(?<![\w./])(?<![A-Za-z]-)(?P<int>\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(?P<dec>\d+))?"
    r"(?!\d|st\b|nd\b|rd\b|th\b)"
    r"(?P<space>\s?)(?P<unit>%|per ?cent\b|crores?\b|cr\b|lakhs?\b|lacs?\b|L\b|k\b|"
    r"million\b|mn\b|billion\b|GWh?\b|MWh?\b|km\b|mins?\b|minutes?\b|hours?\b|hrs?\b|h\b)?",
    re.IGNORECASE,
)
_PLAIN = re.compile(r"\d+(?:\.\d+)?")
_STRIP = [
    re.compile(r"`[^`]*`"),  # code spans
    re.compile(r"\]\([^)]*\)"),  # markdown link targets
    re.compile(r"https?://\S+"),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
    re.compile(r"\b[0-9a-f]{15}\b", re.I),  # H3 cell ids
]
_LIST_MARKER = re.compile(r"^(\s*(?:#+\s*)?)(\d+)([.)]\s)")

_SCALES: dict[str, tuple[float, ...]] = {
    "crore": (1e7, 1),
    "cr": (1e7, 1),
    "lakh": (1e5, 1),
    "lac": (1e5, 1),
    "l": (1e5,),
    "k": (1e3,),
    "million": (1e6,),
    "mn": (1e6,),
    "billion": (1e9,),
    "gw": (1e6, 1e3, 1),
    "gwh": (1e6, 1e3, 1),
    "mw": (1e3, 1),
    "mwh": (1e3, 1),
    "km": (1e3, 1),
    "min": (1, 60),
    "minute": (1, 60),
    "hour": (1, 3600, 60),
    "hr": (1, 3600, 60),
    "h": (1, 3600, 60),
    "%": (1, 0.01),
    "percent": (1, 0.01),
}


def plain_numbers(text: str) -> Iterator[float]:
    for m in _PLAIN.finditer(text):
        yield float(m.group())


@dataclass(frozen=True)
class Claim:
    text: str  # as written, e.g. "₹30.4 crore"
    value: float
    decimals: int
    scales: tuple[float, ...]


def _unit_key(unit: str) -> str:
    u = unit.lower().replace(" ", "")
    if u in ("%", "percent", "percents"):
        return "%"
    for key in ("crore", "lakh", "lac", "minute", "hour", "min", "hr"):
        if u.startswith(key):
            return key
    return u.rstrip("s") if u not in ("mwh", "gwh") else u


def claims(text: str, ignore_years: tuple[int, int] = (1990, 2060)) -> list[Claim]:
    for pattern in _STRIP:
        text = pattern.sub(" ", text)
    out = []
    for line in text.splitlines():
        line = _LIST_MARKER.sub(r"\1 \3", line)
        for m in _NUMBER.finditer(line):
            raw_int, dec, unit = m.group("int"), m.group("dec"), m.group("unit")
            value = float(raw_int.replace(",", "") + (f".{dec}" if dec else ""))
            key = _unit_key(unit) if unit else ""
            prefix = line[max(0, m.start() - 4) : m.start()]
            money = "₹" in prefix or "Rs" in prefix or "INR" in prefix
            if (
                not unit
                and not money
                and not dec
                and "," not in raw_int
                and len(raw_int) == 4
                and ignore_years[0] <= value <= ignore_years[1]
            ):
                continue
            scales = _SCALES.get(key, (1,))
            written = ("₹" if money else "") + m.group(0).strip()
            out.append(Claim(written, value, len(dec or ""), scales))
    return out


class FactIndex:
    """Absolute values of every number seen, sorted for range lookups."""

    def __init__(self, facts: Iterable[float]) -> None:
        self._values = sorted({round(abs(f), 9) for f in facts})

    def __len__(self) -> int:
        return len(self._values)

    def matches(self, claim: Claim, relative_tolerance: float) -> bool:
        half_step = 0.5 * 10 ** (-claim.decimals)
        for scale in claim.scales:
            target = claim.value * scale
            tol = max(half_step * scale, relative_tolerance * target) + 1e-9
            i = bisect.bisect_left(self._values, target - tol)
            if i < len(self._values) and self._values[i] <= target + tol:
                return True
        return False


@dataclass
class Validation:
    checked: int = 0
    unmatched: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unmatched


def _units(markdown: str) -> list[str]:
    """Lines, with prose lines split into sentences (tables and lists stay per line)."""
    out = []
    for line in markdown.split("\n"):
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("|", "#", "-", "*")) or _LIST_MARKER.match(line):
            out.append(line)
            continue
        out.extend(re.split(r"(?<=[.!?])\s+(?=[A-Z₹(\"'])", line))
    return out


def validate(markdown: str, facts: FactIndex, cfg: dict[str, Any]) -> Validation:
    years = tuple(cfg.get("ignore_years", (1990, 2060)))
    tol = float(cfg.get("relative_tolerance", 0.005))
    result = Validation()
    for unit in _units(markdown):
        for claim in claims(unit, years):
            result.checked += 1
            if not facts.matches(claim, tol):
                result.unmatched.append({"number": claim.text, "sentence": unit.strip()})
    return result


def strip_unmatched(markdown: str, facts: FactIndex, cfg: dict[str, Any]) -> tuple[str, list[str]]:
    """Drop every sentence/line holding a number that matches no fact."""
    years = tuple(cfg.get("ignore_years", (1990, 2060)))
    tol = float(cfg.get("relative_tolerance", 0.005))
    kept_lines: list[str] = []
    removed: list[str] = []
    for line in markdown.split("\n"):
        parts = _units(line)
        keep = [p for p in parts if all(facts.matches(c, tol) for c in claims(p, years))]
        removed.extend(p.strip() for p in parts if p not in keep)
        if keep or not line.strip():
            kept_lines.append(" ".join(keep) if len(parts) > 1 else (keep[0] if keep else ""))
    return "\n".join(kept_lines), removed
