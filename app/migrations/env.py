"""Alembic environment.

The app passes an open connection in config.attributes["connection"] when it
upgrades the database at startup. The alembic command line connects with
DATABASE_URL instead.
"""

from alembic import context

from app import models  # noqa: F401  (registers the tables on Base.metadata)
from app.db import Base, make_engine

config = context.config


def run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    run(connection)
else:  # pragma: no cover - command line use
    with make_engine().connect() as conn:
        run(conn)
