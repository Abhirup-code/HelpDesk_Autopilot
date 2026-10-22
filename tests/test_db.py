from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import pytest
from sqlalchemy import inspect, text

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


def test_dashboard_login_reads_only_report_tables():
    if TEST_DB == "sqlite://":
        pytest.skip("PostgreSQL only: SQLite has no logins")
    engine = make_engine(TEST_DB)
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader'"))
        if not exists.first():
            conn.execute(text("CREATE ROLE grafana_reader LOGIN PASSWORD 'test-only'"))
    Base.metadata.drop_all(engine)
    init_db(engine)

    def can_read(table):
        with engine.connect() as conn:
            query = text("SELECT has_table_privilege('grafana_reader', :t, 'SELECT')")
            return conn.execute(query, {"t": table}).scalar()

    assert can_read("tickets") and can_read("audit_events")
    assert not can_read("users")
    assert not can_read("reset_tokens")
    assert not can_read("inbox_messages")


def test_statuses_are_stored_as_readable_values(client, app):
    from tests.conftest import auth

    client.post(
        "/api/tickets",
        json={"type": "password_reset", "title": "Locked out", "description": "MFA"},
        headers=auth("emma"),
    )
    with app.state.session_factory() as session:
        row = session.execute(text("SELECT type, status, priority FROM tickets")).one()
    # Dashboards query these columns directly, so they must match the API's values.
    assert tuple(row) == ("password_reset", "auto_resolved", "P2")
