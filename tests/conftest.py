import os

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token
from app.db import Base, make_engine
from app.directory import InMemoryDirectory
from app.main import create_app
from app.models import Role, User

TOKENS = {"emma": "emma-token", "liam": "liam-token", "ava": "ava-token"}

# Set TEST_DATABASE_URL to run the suite against PostgreSQL instead of in-memory SQLite.
TEST_DB = os.environ.get("TEST_DATABASE_URL", "sqlite://")


@pytest.fixture
def directory():
    directory = InMemoryDirectory.with_default_groups()
    for username, first, last, dept in [
        ("liam", "Liam", "Chen", "Finance"),
        ("emma", "Emma", "Ruiz", "Finance"),
        ("ava", "Ava", "Patel", "IT"),
    ]:
        directory.create_user(username, first, last, dept)
        directory.add_to_group(username, "all-staff")
    return directory


@pytest.fixture
def app(directory):
    if TEST_DB != "sqlite://":
        Base.metadata.drop_all(make_engine(TEST_DB))
    app = create_app(TEST_DB, directory=directory, base_url="http://test")
    with app.state.session_factory() as session:
        manager = User(
            username="liam",
            full_name="Liam Chen",
            department="Finance",
            role=Role.MANAGER,
            token_hash=hash_token(TOKENS["liam"]),
        )
        session.add(manager)
        session.flush()
        session.add_all(
            [
                User(
                    username="emma",
                    full_name="Emma Ruiz",
                    department="Finance",
                    role=Role.EMPLOYEE,
                    manager_id=manager.id,
                    token_hash=hash_token(TOKENS["emma"]),
                ),
                User(
                    username="ava",
                    full_name="Ava Patel",
                    department="IT",
                    role=Role.AGENT,
                    token_hash=hash_token(TOKENS["ava"]),
                ),
            ]
        )
        session.commit()
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[name]}"}
