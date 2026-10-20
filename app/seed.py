"""Create demo users and print their API tokens.

Run once after the database starts:
    docker compose exec api python -m app.seed
"""

from sqlalchemy import select

from app.auth import hash_token, new_token
from app.db import Base, make_engine, make_session_factory
from app.models import Role, User

DEMO_USERS = [
    # username, full name, department, role, manager username
    ("liam", "Liam Chen", "Finance", Role.MANAGER, None),
    ("emma", "Emma Ruiz", "Finance", Role.EMPLOYEE, "liam"),
    ("noah", "Noah Kim", "Engineering", Role.EMPLOYEE, "liam"),
    ("ava", "Ava Patel", "IT", Role.AGENT, None),
]


def seed() -> dict[str, str]:
    engine = make_engine()
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    tokens: dict[str, str] = {}
    with factory() as session:
        for username, full_name, department, role, manager in DEMO_USERS:
            token = new_token()
            user = session.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(username=username, full_name=full_name, department=department)
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
    for name, token in seed().items():
        print(f"{name:6} {token}")
