import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

import app.db.models  # noqa: F401  (registers every model on Base.metadata)
from alembic import context
from app.core.settings import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# Indexes SQLAlchemy can't express on the model (created with op.execute).
HAND_WRITTEN_INDEXES = {"policy_chunk_tsv_idx", "policy_chunk_embedding_idx"}


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Only tables our models own are in scope.

    The same database also holds osm2pgsql's tables and PostGIS's own
    (tiger geocoder, topology, spatial_ref_sys). Autogenerate must never
    propose dropping those; a real drop of one of ours is written by hand.
    """
    table = obj.table.name if type_ in {"index", "column", "unique_constraint"} else name
    if type_ == "index" and reflected and name in HAND_WRITTEN_INDEXES:
        return False
    if type_ in {"table", "index", "column", "unique_constraint"}:
        return table in target_metadata.tables
    return True


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection, target_metadata=target_metadata, include_object=include_object
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(configuration, prefix="sqlalchemy.", poolclass=NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
