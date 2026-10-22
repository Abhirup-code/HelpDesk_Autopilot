"""Directory tests.

Every test runs against the in-memory directory and an ldap3 mock LDAP server.
Set LDAP_TEST_URL (and LDAP_TEST_BIND_PASSWORD) to also run them against a real
OpenLDAP server, e.g. the one in docker-compose.yml.
"""

import os

import pytest
from ldap3 import MOCK_SYNC, Connection, Server

from app.directory import DEFAULT_GROUPS, DirectoryError, InMemoryDirectory, LdapDirectory

BASE = "dc=example,dc=com"
SERVICE_DN = f"cn=autopilot,ou=Services,{BASE}"


def mock_ldap(monkeypatch) -> LdapDirectory:
    conn = Connection(
        Server("mock"), SERVICE_DN, "pw", client_strategy=MOCK_SYNC, raise_exceptions=True
    )
    add = conn.strategy.add_entry
    add(SERVICE_DN, {"objectClass": ["simpleSecurityObject"], "userPassword": "pw"})
    for ou in ("Users", "Groups", "Disabled", "Services"):
        add(f"ou={ou},{BASE}", {"objectClass": ["organizationalUnit"], "ou": ou})
    for group in DEFAULT_GROUPS:
        add(
            f"cn={group},ou=Groups,{BASE}",
            {"objectClass": ["groupOfNames"], "cn": group, "member": [SERVICE_DN]},
        )
    conn.bind()
    conn.unbind = lambda: None  # keep the single mock connection open between calls
    directory = LdapDirectory(lambda: conn, BASE)

    # The mock server has no password-modify operation; record calls instead,
    # and check sign-ins against what was recorded.
    directory.password_changes = []
    passwords: dict[str, str] = {}

    def fake_change(_conn, dn, password):
        directory.password_changes.append(dn)
        passwords[dn] = password

    def fake_bind_as(dn, password):
        return passwords.get(dn) == password

    monkeypatch.setattr(directory, "_change_password", fake_change)
    directory._bind_as = fake_bind_as
    return directory


def real_ldap() -> LdapDirectory:
    url = os.environ["LDAP_TEST_URL"]
    password = os.environ.get("LDAP_TEST_BIND_PASSWORD", "servicepw")
    directory = LdapDirectory(
        lambda: Connection(
            Server(url), SERVICE_DN, password, auto_bind=True, raise_exceptions=True
        ),
        BASE,
        bind_as=lambda dn, pw: Connection(Server(url), dn, pw).bind(),
    )
    for user in ("jdoe", "jdoe2"):
        if directory.user_exists(user):
            for group in directory.groups_of(user):
                directory.remove_from_group(user, group)
            _delete_anywhere(directory, user)
    return directory


def _delete_anywhere(directory: LdapDirectory, username: str) -> None:
    def action(conn):
        dn = directory._find_dn(conn, username)
        conn.delete(dn)

    directory._run(action)


BACKENDS = ["memory", "mock"] + (["real"] if os.environ.get("LDAP_TEST_URL") else [])


@pytest.fixture(params=BACKENDS)
def directory(request, monkeypatch):
    if request.param == "memory":
        return InMemoryDirectory.with_default_groups()
    if request.param == "mock":
        return mock_ldap(monkeypatch)
    return real_ldap()


def test_create_user_and_add_groups(directory):
    assert not directory.user_exists("jdoe")
    directory.create_user("jdoe", "Jane", "Doe", "Finance")
    directory.add_to_group("jdoe", "all-staff")
    directory.add_to_group("jdoe", "finance-share")
    assert directory.user_exists("jdoe")
    assert directory.groups_of("jdoe") == ["all-staff", "finance-share"]
    assert not directory.is_disabled("jdoe")


def test_create_duplicate_user_fails(directory):
    directory.create_user("jdoe", "Jane", "Doe", "Finance")
    with pytest.raises(DirectoryError):
        directory.create_user("jdoe", "Jane", "Doe", "Finance")


def test_remove_from_group(directory):
    directory.create_user("jdoe", "Jane", "Doe", "Finance")
    directory.add_to_group("jdoe", "vpn-users")
    directory.remove_from_group("jdoe", "vpn-users")
    directory.remove_from_group("jdoe", "vpn-users")  # removing twice is harmless
    assert directory.groups_of("jdoe") == []


def test_add_to_unknown_group_fails(directory):
    directory.create_user("jdoe", "Jane", "Doe", "Finance")
    with pytest.raises(DirectoryError):
        directory.add_to_group("jdoe", "no-such-group")


def test_unknown_user_operations_fail(directory):
    with pytest.raises(DirectoryError):
        directory.add_to_group("ghost", "all-staff")
    with pytest.raises(DirectoryError):
        directory.set_password("ghost", "a-long-password-123")
    with pytest.raises(DirectoryError):
        directory.disable_user("ghost")
    with pytest.raises(DirectoryError):
        directory.delete_user("ghost")
    with pytest.raises(DirectoryError):
        directory.is_disabled("ghost")
    assert directory.groups_of("ghost") == []


def test_delete_user_also_leaves_groups(directory):
    directory.create_user("jdoe", "Jane", "Doe", "Finance")
    directory.add_to_group("jdoe", "all-staff")
    directory.delete_user("jdoe")
    assert not directory.user_exists("jdoe")
    assert directory.groups_of("jdoe") == []


def test_set_password_then_disable(directory):
    directory.create_user("jdoe", "Jane", "Doe", "Finance")
    directory.set_password("jdoe", "correct-horse-battery-1")
    directory.disable_user("jdoe")
    assert directory.is_disabled("jdoe")
    with pytest.raises(DirectoryError):
        directory.set_password("jdoe", "another-long-password-2")


def test_ldap_password_change_targets_user_dn(monkeypatch):
    directory = mock_ldap(monkeypatch)
    directory.create_user("jdoe", "Jane", "Doe", "Finance")
    directory.set_password("jdoe", "correct-horse-battery-1")
    assert directory.password_changes == [f"uid=jdoe,ou=Users,{BASE}"]


def test_ldap_refused_password_change_raises():
    class Refusing:
        class extend:  # noqa: N801
            class standard:  # noqa: N801
                @staticmethod
                def modify_password(dn, new_password):
                    return False

    with pytest.raises(DirectoryError):
        LdapDirectory._change_password(Refusing(), "uid=x", "pw")


def test_ldap_connection_failure_is_directory_error():
    def broken():
        raise OSError("connection refused")

    with pytest.raises(DirectoryError, match="Cannot connect"):
        LdapDirectory(broken, BASE).user_exists("jdoe")


def test_ldap_special_characters_are_escaped(monkeypatch):
    directory = mock_ldap(monkeypatch)
    # A filter-injection attempt must not match anything.
    assert not directory.user_exists("*)(uid=*")


def test_from_env(monkeypatch):
    from app.directory import directory_from_env

    monkeypatch.delenv("LDAP_URL", raising=False)
    assert isinstance(directory_from_env(), InMemoryDirectory)
    monkeypatch.setenv("LDAP_URL", "ldap://localhost:1")
    monkeypatch.setenv("LDAP_BIND_PASSWORD", "x")
    ldap_dir = directory_from_env()
    assert isinstance(ldap_dir, LdapDirectory)
    assert ldap_dir.users_ou == f"ou=Users,{BASE}"
    with pytest.raises(DirectoryError):
        ldap_dir.user_exists("jdoe")


def test_authenticate(directory):
    directory.create_user("jdoe", "Jane", "Doe", "Finance")
    assert not directory.authenticate("jdoe", "anything")  # no password set yet
    directory.set_password("jdoe", "correct-horse-battery-1")
    assert directory.authenticate("jdoe", "correct-horse-battery-1")
    assert not directory.authenticate("jdoe", "wrong-password-123")
    assert not directory.authenticate("jdoe", "")
    assert not directory.authenticate("ghost", "correct-horse-battery-1")
    directory.disable_user("jdoe")
    assert not directory.authenticate("jdoe", "correct-horse-battery-1")


def test_ldap_authenticate_without_bind_or_on_errors():
    def broken():
        raise OSError("down")

    assert not LdapDirectory(broken, BASE).authenticate("jdoe", "pw")  # no bind_as
    assert not LdapDirectory(broken, BASE, bind_as=lambda dn, pw: True).authenticate("jdoe", "pw")
