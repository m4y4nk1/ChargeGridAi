from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _fresh_db_pool() -> Iterator[None]:
    """Each async test runs on its own event loop; pooled asyncpg connections belong to
    the loop that opened them, so start every test with an empty pool."""
    yield
    from app.db.session import engine

    engine.sync_engine.dispose(close=False)
