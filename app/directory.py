"""Company directory (LDAP) operations used by the automations.

`LdapDirectory` talks to OpenLDAP (or any LDAP server, including Active
Directory with small changes). `InMemoryDirectory` behaves the same way and is
used for local development without LDAP and in fast unit tests.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ldap3 import (
    MODIFY_ADD,
    MODIFY_DELETE,
    MODIFY_REPLACE,
    SUBTREE,
    Connection,
    Server,
)
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn


class DirectoryError(Exception):
    """A directory operation failed."""


class Directory(Protocol):
    def user_exists(self, username: str) -> bool: ...
    def create_user(self, username: str, first: str, last: str, department: str) -> None: ...
    def delete_user(self, username: str) -> None: ...
    def add_to_group(self, username: str, group: str) -> None: ...
    def remove_from_group(self, username: str, group: str) -> None: ...
    def groups_of(self, username: str) -> list[str]: ...
    def set_password(self, username: str, password: str) -> None: ...
    def disable_user(self, username: str) -> None: ...
    def is_disabled(self, username: str) -> bool: ...


# ---------------------------------------------------------------------------
# In-memory version
# ---------------------------------------------------------------------------


@dataclass
class _Person:
    first: str
    last: str
    department: str
    password_set: bool = False
    disabled: bool = False


@dataclass
class InMemoryDirectory:
    groups: dict[str, set[str]] = field(default_factory=dict)
    people: dict[str, _Person] = field(default_factory=dict)
    fail_on_group: str | None = None  # lets tests simulate a failure

    @classmethod
    def with_default_groups(cls) -> "InMemoryDirectory":
        return cls(groups={g: set() for g in DEFAULT_GROUPS})

    def _person(self, username: str) -> _Person:
        if username not in self.people:
            raise DirectoryError(f"No such user: {username}")
        return self.people[username]

    def user_exists(self, username: str) -> bool:
        return username in self.people

    def create_user(self, username: str, first: str, last: str, department: str) -> None:
        if username in self.people:
            raise DirectoryError(f"User already exists: {username}")
        self.people[username] = _Person(first, last, department)

    def delete_user(self, username: str) -> None:
        self._person(username)
        for members in self.groups.values():
            members.discard(username)
        del self.people[username]

    def add_to_group(self, username: str, group: str) -> None:
        self._person(username)
        if group == self.fail_on_group or group not in self.groups:
            raise DirectoryError(f"Cannot add {username} to {group}")
        self.groups[group].add(username)

    def remove_from_group(self, username: str, group: str) -> None:
        self.groups.get(group, set()).discard(username)

    def groups_of(self, username: str) -> list[str]:
        return sorted(g for g, members in self.groups.items() if username in members)

    def set_password(self, username: str, password: str) -> None:
        person = self._person(username)
        if person.disabled:
            raise DirectoryError(f"Account is disabled: {username}")
        person.password_set = True

    def disable_user(self, username: str) -> None:
        person = self._person(username)
        person.disabled = True
        person.password_set = False

    def is_disabled(self, username: str) -> bool:
        return self._person(username).disabled


# ---------------------------------------------------------------------------
# LDAP version
# ---------------------------------------------------------------------------

DEFAULT_GROUPS = [
    "all-staff",
    "finance-share",
    "erp-users",
    "git-users",
    "vpn-users",
    "tableau-users",
    "helpdesk-agents",
    "it-admins",
]


class LdapDirectory:
    def __init__(self, connect: Callable[[], Connection], base_dn: str) -> None:
        self._connect = connect
        self.base = base_dn
        self.users_ou = f"ou=Users,{base_dn}"
        self.disabled_ou = f"ou=Disabled,{base_dn}"
        self.groups_ou = f"ou=Groups,{base_dn}"

    @classmethod
    def from_env(cls) -> "LdapDirectory":
        url = os.environ["LDAP_URL"]
        base = os.environ.get("LDAP_BASE_DN", "dc=example,dc=com")
        bind_dn = os.environ.get("LDAP_BIND_DN", f"cn=autopilot,ou=Services,{base}")
        password = os.environ["LDAP_BIND_PASSWORD"]

        def connect() -> Connection:
            return Connection(Server(url), bind_dn, password, auto_bind=True, raise_exceptions=True)

        return cls(connect, base)

    def _user_dn(self, username: str, disabled: bool = False) -> str:
        ou = self.disabled_ou if disabled else self.users_ou
        return f"uid={escape_rdn(username)},{ou}"

    def _group_dn(self, group: str) -> str:
        return f"cn={escape_rdn(group)},{self.groups_ou}"

    def _run(self, action: Callable[[Connection], object]):
        try:
            conn = self._connect()
        except Exception as exc:
            raise DirectoryError(f"Cannot connect to directory: {exc}") from exc
        try:
            return action(conn)
        except DirectoryError:
            raise
        except Exception as exc:
            raise DirectoryError(str(exc)) from exc
        finally:
            conn.unbind()

    def _find_dn(self, conn: Connection, username: str) -> str | None:
        conn.search(
            self.base,
            f"(&(objectClass=inetOrgPerson)(uid={escape_filter_chars(username)}))",
            SUBTREE,
            attributes=["uid"],
        )
        return conn.entries[0].entry_dn if conn.entries else None

    def user_exists(self, username: str) -> bool:
        return bool(self._run(lambda c: self._find_dn(c, username)))

    def is_disabled(self, username: str) -> bool:
        def action(conn: Connection) -> bool:
            dn = self._find_dn(conn, username)
            if dn is None:
                raise DirectoryError(f"No such user: {username}")
            return dn.lower().endswith(self.disabled_ou.lower())

        return self._run(action)

    def create_user(self, username: str, first: str, last: str, department: str) -> None:
        def action(conn: Connection) -> None:
            conn.add(
                self._user_dn(username),
                ["inetOrgPerson"],
                {
                    "uid": username,
                    "cn": f"{first} {last}",
                    "givenName": first,
                    "sn": last,
                    "departmentNumber": department,
                    "mail": f"{username}@example.com",
                },
            )

        self._run(action)

    def delete_user(self, username: str) -> None:
        def action(conn: Connection) -> None:
            dn = self._find_dn(conn, username)
            if dn is None:
                raise DirectoryError(f"No such user: {username}")
            for group in self._groups(conn, dn):
                conn.modify(self._group_dn(group), {"member": [(MODIFY_DELETE, [dn])]})
            conn.delete(dn)

        self._run(action)

    def add_to_group(self, username: str, group: str) -> None:
        def action(conn: Connection) -> None:
            dn = self._find_dn(conn, username)
            if dn is None:
                raise DirectoryError(f"No such user: {username}")
            conn.modify(self._group_dn(group), {"member": [(MODIFY_ADD, [dn])]})

        self._run(action)

    def remove_from_group(self, username: str, group: str) -> None:
        def action(conn: Connection) -> None:
            dn = self._find_dn(conn, username)
            if dn is not None and group in self._groups(conn, dn):
                conn.modify(self._group_dn(group), {"member": [(MODIFY_DELETE, [dn])]})

        self._run(action)

    def _groups(self, conn: Connection, dn: str) -> list[str]:
        conn.search(
            self.groups_ou,
            f"(&(objectClass=groupOfNames)(member={escape_filter_chars(dn)}))",
            SUBTREE,
            attributes=["cn"],
        )
        return sorted(str(e.cn) for e in conn.entries)

    def groups_of(self, username: str) -> list[str]:
        def action(conn: Connection) -> list[str]:
            dn = self._find_dn(conn, username)
            return self._groups(conn, dn) if dn else []

        return self._run(action)

    def set_password(self, username: str, password: str) -> None:
        def action(conn: Connection) -> None:
            dn = self._find_dn(conn, username)
            if dn is None:
                raise DirectoryError(f"No such user: {username}")
            if dn.lower().endswith(self.disabled_ou.lower()):
                raise DirectoryError(f"Account is disabled: {username}")
            self._change_password(conn, dn, password)

        self._run(action)

    @staticmethod
    def _change_password(conn: Connection, dn: str, password: str) -> None:
        # RFC 3062 password modify: the server hashes it (Argon2id in our config),
        # so the app never handles password hashes.
        if not conn.extend.standard.modify_password(dn, new_password=password):
            raise DirectoryError("Directory refused the password change")

    def disable_user(self, username: str) -> None:
        """Remove the password and move the account to ou=Disabled.

        Group removal is done by the caller first, so each removal is audited.
        """

        def action(conn: Connection) -> None:
            dn = self._find_dn(conn, username)
            if dn is None:
                raise DirectoryError(f"No such user: {username}")
            conn.modify(dn, {"userPassword": [(MODIFY_REPLACE, [])]})
            conn.modify_dn(dn, f"uid={escape_rdn(username)}", new_superior=self.disabled_ou)

        self._run(action)


def directory_from_env() -> Directory:
    if os.environ.get("LDAP_URL"):
        return LdapDirectory.from_env()
    return InMemoryDirectory.with_default_groups()
