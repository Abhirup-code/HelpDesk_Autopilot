from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from sqlalchemy import inspect

from app.db import Base, init_db, make_engine
from tests.conftest import TEST_DB


def _start_process(url: str) -> None:
    init_db(make_engine(url))


def test_init_db_is_safe_when_processes_start_together(tmp_path):
    # Simulates the api and worker containers starting at the same moment.
    # PostgreSQL runs real separate processes, which only the database lock can
    # keep in order. SQLite is single-process, so threads stand in for it.
    if TEST_DB != "sqlite://":
        url, pool = TEST_DB, ProcessPoolExecutor(max_workers=4)
    else:
        url, pool = f"sqlite:///{tmp_path}/race.db", ThreadPoolExecutor(max_workers=4)
    Base.metadata.drop_all(make_engine(url))

    with pool:
        list(pool.map(_start_process, [url] * 4))  # re-raises any crash

    tables = set(inspect(make_engine(url)).get_table_names())
    assert {"users", "tickets", "audit_events"} <= tables


def test_init_db_twice_is_harmless(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/twice.db")
    init_db(engine)
    init_db(engine)
