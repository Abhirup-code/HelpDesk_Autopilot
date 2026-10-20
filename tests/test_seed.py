from sqlalchemy import select

from app import seed as seed_module
from app.auth import hash_token
from app.db import make_engine, make_session_factory
from app.directory import InMemoryDirectory
from app.models import User


def test_seed_creates_users_and_rotates_tokens(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/seed.db"
    monkeypatch.setenv("DATABASE_URL", url)

    first = seed_module.seed()
    second = seed_module.seed()

    assert set(first) == {"liam", "emma", "noah", "ava"}
    assert first["emma"] != second["emma"]

    with make_session_factory(make_engine(url))() as session:
        users = {u.username: u for u in session.scalars(select(User))}
        assert len(users) == 4
        assert users["emma"].manager_id == users["liam"].id
        assert users["emma"].token_hash == hash_token(second["emma"])


def test_seed_creates_directory_accounts_once(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/seed.db")
    directory = InMemoryDirectory.with_default_groups()

    seed_module.seed(directory)
    assert directory.groups_of("noah") == ["all-staff", "git-users", "vpn-users"]
    assert directory.groups_of("ava") == ["all-staff", "helpdesk-agents"]

    assert seed_module.seed_directory(directory) == []  # already there, nothing new
