import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token
from app.main import create_app
from app.models import Role, User

TOKENS = {"emma": "emma-token", "liam": "liam-token", "ava": "ava-token"}


@pytest.fixture
def app():
    app = create_app("sqlite://")
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
