"""Model assumptions with placeholder tracking (Section 16).

Config parameters are written as `{value, illustrative, source, as_of}`. A
numeric `value` is a sourced number; otherwise the engine falls back to
`illustrative` and records the parameter as a placeholder in use, so every
output can say exactly which of its inputs are stand-ins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.settings import get_settings


class MissingAssumption(ValueError):
    pass


@dataclass
class Assumptions:
    """Resolves config parameters and remembers which ones were placeholders."""

    placeholders: set[str] = field(default_factory=set)
    sources: dict[str, str] = field(default_factory=dict)

    def get(self, node: Any, key: str) -> float:
        if isinstance(node, int | float):
            return float(node)
        if not isinstance(node, dict):
            raise MissingAssumption(f"{key}: expected a number or {{value, illustrative}}")
        value = node.get("value")
        if isinstance(value, int | float):
            self.sources[key] = str(node.get("source") or "unspecified")
            return float(value)
        fallback = node.get("illustrative")
        if isinstance(fallback, int | float):
            self.placeholders.add(key)
            return float(fallback)
        raise MissingAssumption(f"{key} has neither a sourced value nor an illustrative fallback")

    @property
    def placeholders_in_use(self) -> list[str]:
        return sorted(self.placeholders)


def load_config(*parts: str) -> dict[str, Any]:
    """A config/ file, or its latest admin-edited version (app.core.config_store)."""
    from app.core.config_store import overrides

    key = "/".join(parts)
    override = overrides().get(key)
    if override is not None:
        data: dict[str, Any] = yaml.safe_load(override)
        return data
    path: Path = get_settings().config_dir.joinpath(*parts)
    data = yaml.safe_load(path.read_text())
    return data
