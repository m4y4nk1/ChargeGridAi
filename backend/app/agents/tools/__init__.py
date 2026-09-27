"""The agents' tool registry (Section 10.2)."""

from app.agents.tools import data, geo, planning, results, sites, twin
from app.agents.tools.base import Tool, ToolContext, ToolError, ToolOutcome, execute

ALL_TOOLS: list[Tool] = [
    *data.TOOLS,
    *geo.TOOLS,
    *planning.TOOLS,
    *sites.TOOLS,
    *results.TOOLS,
    *twin.TOOLS,
]
BY_API_NAME: dict[str, Tool] = {t.api_name: t for t in ALL_TOOLS}


def tools_for(grants: tuple[str, ...]) -> list[Tool]:
    """Tools an agent may call: a grant is a group ("geo") or a dotted tool name."""
    return [t for t in ALL_TOOLS if t.group in grants or t.name in grants]


__all__ = [
    "ALL_TOOLS",
    "BY_API_NAME",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolOutcome",
    "execute",
    "tools_for",
]
