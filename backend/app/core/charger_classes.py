"""Power bands used across the product (Section 3, Area Planner charts)."""

POWER_BANDS = ("AC", "DC <60 kW", "DC 60–149 kW", "DC 150–349 kW", "DC ≥350 kW", "Unknown")


def power_band(current: str | None, max_kw: float | None) -> str:
    """Band for one charge point. AC is one band regardless of kW (Section 3: "AC ≤22 kW")."""
    if current == "AC" or (current is None and max_kw is not None and max_kw <= 22):
        return "AC"
    if max_kw is None:
        return "Unknown"
    if max_kw < 60:
        return "DC <60 kW"
    if max_kw < 150:
        return "DC 60–149 kW"
    if max_kw < 350:
        return "DC 150–349 kW"
    return "DC ≥350 kW"
