"""Database engine and session setup."""

import os
import threading
import weakref
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_URL = "sqlite:///./helpdesk.db"


class Base(DeclarativeBase):
    pass


# Every engine created, so tests can close their connection pools afterwards.
ENGINES: "weakref.WeakSet[Engine]" = weakref.WeakSet()


def make_engine(url: str | None = None) -> Engine:
    engine = _create(url or os.environ.get("DATABASE_URL", DEFAULT_URL))
    ENGINES.add(engine)
    return engine


def _create(url: str) -> Engine:
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    # Keep every timestamp in UTC, whatever the server time zone is.
    return create_engine(url, pool_pre_ping=True, connect_args={"options": "-c timezone=utc"})


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def session_dependency(factory: sessionmaker[Session]):
    def get_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    return get_session


# Any fixed number works; it just has to be the same in every process.
SCHEMA_LOCK_ID = 724_001
# SQLite has no advisory locks. It is only used for local runs and tests, where
# everything shares one process, so a normal thread lock is enough.
_local_schema_lock = threading.Lock()


MIGRATIONS = Path(__file__).resolve().parent / "migrations"
# The revision that matches databases created before migrations were added.
FIRST_REVISION = "0001"


def run_migrations(conn) -> None:
    """Bring the database schema up to date, keeping existing data."""
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["connection"] = conn
    tables = set(inspect(conn).get_table_names())
    if "alembic_version" not in tables and "tickets" in tables:
        # Built by an older version with create_all: record it as the first
        # revision instead of trying to create the tables again.
        command.stamp(config, FIRST_REVISION)
    command.upgrade(config, "head")


def init_db(engine: Engine) -> None:
    """Create or upgrade the schema. Safe when several processes start at once.

    The api and worker containers start together. Without a lock, both try to
    create the same tables and one crashes. On PostgreSQL an advisory lock makes
    them take turns; the second one finds the tables already there.
    """
    with _local_schema_lock, engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": SCHEMA_LOCK_ID})
        run_migrations(conn)
        if conn.dialect.name == "postgresql":
            grant_dashboard_access(conn)


# Tables the Grafana dashboard may read. Users, tokens and reset links stay private.
DASHBOARD_TABLES = ("tickets", "audit_events")


def grant_dashboard_access(conn) -> None:
    """Let the read-only grafana_reader login (created by db/init) read report tables."""
    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader'"))
    if exists.first():
        conn.execute(text(f"GRANT SELECT ON {', '.join(DASHBOARD_TABLES)} TO grafana_reader"))
