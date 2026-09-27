"""add app_user table

Revision ID: 4fe7edb3b871
Revises:
Create Date: 2026-09-23 18:22:28.016482

Note: autogenerate also proposed dropping road_segment/poi/grid_asset/
admin_boundary/osm2pgsql_properties/spatial_ref_sys — those are owned by
the osm2pgsql flex import (app/providers/osm/import_pbf.py), not this
Alembic history, and are intentionally left out here. See app/db/base.py.
"""

from collections.abc import Sequence

import fastapi_users_db_sqlalchemy
import sqlalchemy as sa

from alembic import op

revision: str = "4fe7edb3b871"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_user_email"), "app_user", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_app_user_email"), table_name="app_user")
    op.drop_table("app_user")
