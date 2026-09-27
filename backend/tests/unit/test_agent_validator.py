"""Numeric-claim validator (Section 10.3)."""

from app.agents.validator import FactIndex, claims, strip_unmatched, validate

CFG = {"relative_tolerance": 0.005, "ignore_years": [1990, 2060]}


def texts(s: str) -> list[str]:
    return [c.text for c in claims(s)]


def test_extracts_money_percent_units_and_skips_identifiers() -> None:
    found = texts(
        "In 2030 the plan costs ₹28.78 crore for 20 sites (DC_120, MH12, P50, NH48) with "
        "12.5% utilisation, 1.2 MW at peak and 3,40,000 kWh; see `run 42` or "
        "https://x.org/7 and the 3rd site."
    )
    assert found == ["₹28.78 crore", "20", "12.5%", "1.2 MW", "3,40,000"]


def test_list_markers_are_not_claims() -> None:
    assert texts("1. First site\n2) Second site\n## 3. Heading") == []


def test_crore_matches_inr_fact_at_displayed_precision() -> None:
    facts = FactIndex([287_760_000])
    (c,) = claims("₹28.78 crore")
    assert facts.matches(c, 0.005)
    (c,) = claims("₹28.8 crore")
    assert facts.matches(c, 0.005)
    (c,) = claims("₹30 crore")
    assert not facts.matches(c, 0.005)


def test_percent_matches_fraction_and_sign_is_ignored() -> None:
    facts = FactIndex([0.1368, -6_188_792.8])
    assert facts.matches(claims("an IRR of 13.7%")[0], 0.005)
    assert facts.matches(claims("a loss of ₹61.9 lakh")[0], 0.005)
    assert facts.matches(claims("NPV of −₹0.62 crore")[0], 0.005)


def test_unit_rescaling() -> None:
    facts = FactIndex([1200.0, 147_000.0, 6540.0])
    assert facts.matches(claims("1.2 MW")[0], 0.005)  # kW fact
    assert facts.matches(claims("147 km")[0], 0.005)  # metres fact
    assert facts.matches(claims("109 min")[0], 0.005)  # seconds fact


def test_validate_and_strip() -> None:
    facts = FactIndex([20, 287_760_000])
    md = (
        "## Answer\n\nThe plan selects 20 sites. It costs ₹28.78 crore. It pays back in "
        "7 years.\n\n| Site | kW |\n|---|---|\n| A | 999 |\n| B | 20 |"
    )
    v = validate(md, facts, CFG)
    assert v.checked == 5
    assert [u["number"] for u in v.unmatched] == ["7", "999"]
    cleaned, removed = strip_unmatched(md, facts, CFG)
    assert "7 years" not in cleaned and "| A | 999 |" not in cleaned
    assert "The plan selects 20 sites. It costs ₹28.78 crore." in cleaned
    assert "| B | 20 |" in cleaned
    assert len(removed) == 2
    assert validate(cleaned, facts, CFG).ok


def test_years_ignored_but_not_money() -> None:
    assert texts("By 2030, and ₹2030") == ["₹2030"]
