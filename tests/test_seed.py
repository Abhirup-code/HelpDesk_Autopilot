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


def test_seed_directory_creates_accounts_without_passwords():
    directory = InMemoryDirectory.with_default_groups()
    active = seed_module.seed_directory(directory)
    assert active == ["liam", "emma", "noah", "ava"]
    assert directory.groups_of("noah") == ["all-staff", "git-users", "vpn-users"]
    assert directory.groups_of("ava") == ["all-staff", "helpdesk-agents"]
    assert not directory.people["emma"].password_set  # nobody's password is chosen for them

    assert seed_module.seed_directory(directory) == active  # running again changes nothing
    assert directory.groups_of("noah") == ["all-staff", "git-users", "vpn-users"]


def test_seed_skips_offboarded_accounts():
    directory = InMemoryDirectory.with_default_groups()
    seed_module.seed_directory(directory)
    directory.disable_user("noah")
    assert "noah" not in seed_module.seed_directory(directory)


def test_setup_links_let_people_choose_a_password(tmp_path, monkeypatch):
    from app import services
    from app.models import AuditEvent

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/seed.db")
    seed_module.seed()
    directory = InMemoryDirectory.with_default_groups()
    active = seed_module.seed_directory(directory)

    links = seed_module.setup_links(active, "http://portal.test/")
    assert set(links) == set(active)
    assert all(link.startswith("http://portal.test/reset/") for link in links.values())

    token = links["emma"].rsplit("/", 1)[1]
    with make_session_factory(make_engine())() as session:
        services.redeem_link(session, directory, token, "my-own-choice-77")
        issued = session.query(AuditEvent).filter_by(action="password_link.issue").count()
    assert directory.authenticate("emma", "my-own-choice-77")
    assert issued == 4
