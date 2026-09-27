"""Versioned system prompts (Section 10.3): one Markdown file per agent in this package,
with a front-matter header naming it and its version. The version is logged with every
model call, so a change in behaviour can be traced to a prompt change."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str


@lru_cache
def load_prompt(name: str) -> Prompt:
    raw = (_DIR / f"{name}.md").read_text()
    if not raw.startswith("---\n"):
        raise ValueError(f"prompt {name} has no front matter")
    header, body = raw[4:].split("\n---\n", 1)
    meta = dict(line.split(":", 1) for line in header.strip().splitlines())
    shared = (_DIR / "_shared.md").read_text().split("\n---\n", 1)[-1].strip()
    text = body.strip() + "\n\n" + shared
    return Prompt(name=meta["name"].strip(), version=meta["version"].strip(), text=text)
