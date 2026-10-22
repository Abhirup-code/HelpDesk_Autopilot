"""Create demo users, give them directory accounts, and print tokens and password links.

Run after the stack starts:
    docker compose exec api python -m app.seed
"""

import os

from sqlalchemy import select

from app.auth import hash_token, new_token
from app.automation import issue_password_link
from app.db import init_db, make_engine, make_session_factory
from app.directory import Directory, DirectoryError, InMemoryDirectory, directory_from_env
from app.models import Role, User
from app.policy import DEPARTMENT_GROUPS, NEW_HIRE_LINK_TTL

DEMO_USERS = [
    # username, first, last, department, role, manager username
    ("liam", "Liam", "Chen", "Finance", Role.MANAGER, None),
    ("emma", "Emma", "Ruiz", "Finance", Role.EMPLOYEE, "liam"),
    ("noah", "Noah", "Kim", "Engineering", Role.EMPLOYEE, "liam"),
    ("ava", "Ava", "Patel", "IT", Role.AGENT, None),
]


def seed_directory(directory: Directory) -> list[str]:
    """Give each demo user an active directory account. Returns who can sign in.

    No passwords are set or shown here: each person gets a one-time link to
    choose their own, the same way a real new hire does. Accounts offboarded in
    an earlier demo are left disabled.
    """
    active = []
    for username, first, last, department, _, _ in DEMO_USERS:
        if not directory.user_exists(username):
            directory.create_user(username, first, last, department)
            for group in DEPARTMENT_GROUPS[department]:
                directory.add_to_group(username, group)
        if not directory.is_disabled(username):
            active.append(username)
    return active


def setup_links(usernames: list[str], base_url: str) -> dict[str, str]:
    """One-time links (valid 72 hours) for each person to set their portal password."""
    factory = make_session_factory(make_engine())
    with factory() as session:
        links = {
            name: issue_password_link(session, name, base_url, NEW_HIRE_LINK_TTL)
            for name in usernames
        }
        session.commit()
    return links


def seed(directory: Directory | None = None) -> dict[str, str]:
    """Create demo portal users and return their API tokens."""
    engine = make_engine()
    init_db(engine)
    factory = make_session_factory(engine)
    tokens: dict[str, str] = {}
    with factory() as session:
        for username, first, last, department, role, manager in DEMO_USERS:
            token = new_token()
            user = session.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(username=username, full_name=f"{first} {last}", department=department)
                session.add(user)
            user.role = role
            user.token_hash = hash_token(token)
            if manager:
                session.flush()
                boss = session.scalar(select(User).where(User.username == manager))
                user.manager_id = boss.id if boss else None
            tokens[username] = token
        session.commit()
    return tokens


if __name__ == "__main__":  # pragma: no cover
    PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000")
    directory = directory_from_env()
    if isinstance(directory, InMemoryDirectory):
        print("LDAP_URL not set: skipping directory accounts")
        directory = None
    tokens = seed()
    links: dict[str, str] = {}
    if directory is not None:
        try:
            active = seed_directory(directory)
        except DirectoryError as exc:
            raise SystemExit(f"Could not create directory accounts: {exc}") from exc
        links = setup_links(active, PUBLIC_URL)
    # Columns: user, API token (for /docs and curl), one-time link to set a portal password.
    for name, token in tokens.items():
        print(f"{name:6} {token}  {links.get(name, '-')}")
    print(f"\nOpen a link to choose that person's password, then sign in at {PUBLIC_URL}")
