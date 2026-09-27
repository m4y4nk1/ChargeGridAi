"""Versioned config overrides (PUT /admin/config/{key}).

config/ files are the defaults shipped with the code; an admin edit stores a new
version in `config_version`, and the latest version of a key overrides the file.
Every process reads the overrides with a short-lived cache (30 s), so an edit reaches
the API immediately (the cache is cleared on write) and workers within a task.
"""

from __future__ import annotations

import logging
import time

from app.core.settings import get_settings

log = logging.getLogger(__name__)
TTL_S = 30.0
_cache: tuple[float, dict[str, str]] | None = None


def _sync_url() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


def overrides() -> dict[str, str]:
    global _cache
    if _cache is not None and time.monotonic() - _cache[0] < TTL_S:
        return _cache[1]
    try:
        import psycopg

        with psycopg.connect(_sync_url(), connect_timeout=2) as conn:
            rows = conn.execute(
                "select distinct on (key) key, content from config_version "
                "order by key, version desc"
            ).fetchall()
        result = {k: c for k, c in rows}
    except Exception as exc:  # no database (unit tests, CLI before migrate): files only
        log.debug("config overrides unavailable: %s", exc)
        result = {}
    _cache = (time.monotonic(), result)
    return result


def invalidate() -> None:
    global _cache
    _cache = None
