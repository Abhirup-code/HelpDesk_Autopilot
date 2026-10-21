"""Database engine and session setup."""

import os
import threading
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_URL = "sqlite:///./helpdesk.db"


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None) -> Engine:
    url = url or os.environ.get("DATABASE_URL", DEFAULT_URL)
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


def init_db(engine: Engine) -> None:
    """Create missing tables. Safe when several processes start at once.

    The api and worker containers start together. Without a lock, both try to
    create the same tables and one crashes. On PostgreSQL an advisory lock makes
    them take turns; the second one finds the tables already there.
    """
    with _local_schema_lock, engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": SCHEMA_LOCK_ID})
        Base.metadata.create_all(conn)
