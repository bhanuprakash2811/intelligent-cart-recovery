"""
pytest fixtures for YottaFlex Cart Recovery tests.

Uses SQLite in-memory so tests run without Docker or PostgreSQL.
"""

from __future__ import annotations

import os
import pytest

# ---------------------------------------------------------------------------
# Point SQLAlchemy at an in-memory SQLite database before importing app code
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")


@pytest.fixture(scope="session")
def db_engine():
    """Create all tables and return the engine. Shared across the session."""
    from app.db import engine, Base
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="session")
def small_gen(db_engine):
    """
    Run the generator at --small scale (100 carts) once per session.
    Returns the summary dict from generate.run().
    """
    from app.generate import run
    summary = run(
        seed=20261022,
        n_customers=50,
        n_carts=100,
        n_abandoned=40,
        n_sends=20,
        reset=True,
        export_csv=False,
    )
    return summary


@pytest.fixture
def db_session(db_engine, small_gen):
    """Return a fresh SQLAlchemy Session over the in-memory test DB."""
    from app.db import SessionLocal
    db = SessionLocal()
    yield db
    db.close()
