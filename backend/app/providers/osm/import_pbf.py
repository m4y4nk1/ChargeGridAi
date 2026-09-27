"""One-shot / repeatable OSM import into PostGIS via osm2pgsql flex output.

Phase 1 scope: a single full import of the configured extract. Phase 1's
`ingest_osm` Celery Beat schedule (Section 13) will wrap this same command
for daily diffs via pyosmium once real recurring ingestion is needed —
this module is the CLI entry point that job will call.

The four tables this produces (road_segment, poi, grid_asset,
admin_boundary) are owned end-to-end by osm2pgsql's flex output: it creates
and repopulates them on every run from `flex_style.lua`. They are
deliberately NOT Alembic-managed, since Alembic and osm2pgsql would fight
over the same schema otherwise.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.core.settings import get_settings

STYLE_PATH = Path(__file__).with_name("flex_style.lua")


def import_pbf(pbf_path: Path) -> None:
    settings = get_settings()
    db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    subprocess.run(
        [
            "osm2pgsql",
            "--create",
            "--output=flex",
            f"--style={STYLE_PATH}",
            "-d",
            db_url,
            str(pbf_path),
        ],
        check=True,
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m app.providers.osm.import_pbf <path-to-pbf-or-osm-xml>")
        raise SystemExit(1)
    import_pbf(Path(sys.argv[1]))
