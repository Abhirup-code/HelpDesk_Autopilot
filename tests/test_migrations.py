"""Migrations must build exactly the schema the models describe, and upgrades
must keep existing data."""

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from app.db import Base, init_db, make_engine
from tests.conftest import TEST_DB, drop_everything


def fresh_engine(tmp_path):
    if TEST_DB != "sqlite://":
        engine = make_engine(TEST_DB)
        drop_everything(engine)
        return engine
    return make_engine(f"sqlite:///{tmp_path}/migrate.db")


def version(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_migrations_match_the_models(tmp_path):
    engine = fresh_engine(tmp_path)
    init_db(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migrations differ; add a migration: {diff}"


def test_upgrading_keeps_existing_data(tmp_path):
    engine = fresh_engine(tmp_path)
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (username, full_name, department, role, token_hash) "
                "VALUES ('keep', 'Keep Me', 'IT', 'agent', 'h')"
            )
        )
    init_db(engine)  # as on the next deploy
    with engine.connect() as conn:
        assert conn.execute(text("SELECT full_name FROM users")).scalar() == "Keep Me"
    assert version(engine) == "0001"


def test_database_from_before_migrations_is_adopted(tmp_path):
    # Older versions built tables with create_all and had no alembic_version table.
    engine = fresh_engine(tmp_path)
    Base.metadata.create_all(engine)
    assert "alembic_version" not in inspect(engine).get_table_names()
    init_db(engine)
    assert version(engine) == "0001"


def test_downgrade_and_upgrade_again(tmp_path):
    from alembic import command
    from alembic.config import Config

    from app.db import MIGRATIONS

    engine = fresh_engine(tmp_path)
    init_db(engine)
    with engine.begin() as conn:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS))
        config.attributes["connection"] = conn
        command.downgrade(config, "base")
    assert "tickets" not in inspect(engine).get_table_names()
    init_db(engine)
    assert "tickets" in inspect(engine).get_table_names()
