"""Create demo users, give them directory accounts, and print their API tokens.

Run after the stack starts:
    docker compose exec api python -m app.seed
"""

from sqlalchemy import select

from app.auth import hash_token, new_token
from app.db import Base, make_engine, make_session_factory
from app.directory import Directory, DirectoryError, InMemoryDirectory, directory_from_env
from app.models import Role, User
from app.policy import DEPARTMENT_GROUPS

DEMO_USERS = [
    # username, first, last, department, role, manager username
    ("liam", "Liam", "Chen", "Finance", Role.MANAGER, None),
    ("emma", "Emma", "Ruiz", "Finance", Role.EMPLOYEE, "liam"),
    ("noah", "Noah", "Kim", "Engineering", Role.EMPLOYEE, "liam"),
    ("ava", "Ava", "Patel", "IT", Role.AGENT, None),
]


def seed_directory(directory: Directory) -> list[str]:
    """Create directory accounts for demo users who don't have one yet."""
    created = []
    for username, first, last, department, _, _ in DEMO_USERS:
        if directory.user_exists(username):
            continue
        directory.create_user(username, first, last, department)
        for group in DEPARTMENT_GROUPS[department]:
            directory.add_to_group(username, group)
        created.append(username)
    return created


def seed(directory: Directory | None = None) -> dict[str, str]:
    engine = make_engine()
    Base.metadata.create_all(engine)
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
    if directory is not None:
        seed_directory(directory)
    return tokens


if __name__ == "__main__":  # pragma: no cover
    directory = directory_from_env()
    if isinstance(directory, InMemoryDirectory):
        print("LDAP_URL not set: skipping directory accounts")
        directory = None
    try:
        tokens = seed(directory)
    except DirectoryError as exc:
        raise SystemExit(f"Could not create directory accounts: {exc}") from exc
    for name, token in tokens.items():
        print(f"{name:6} {token}")
