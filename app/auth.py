"""Bearer token authentication.

Each user gets a random API token. Only its SHA-256 hash is stored, so a
database leak does not expose working tokens. A real login page replaces
this in a later step.
"""

import hashlib
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Role, User

bearer = HTTPBearer(auto_error=False)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def make_current_user(get_session):
    def current_user(
        creds: HTTPAuthorizationCredentials | None = Depends(bearer),
        session: Session = Depends(get_session),
    ) -> User:
        if creds is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
        user = session.scalar(select(User).where(User.token_hash == hash_token(creds.credentials)))
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
        return user

    return current_user


def require_agent(user: User) -> None:
    if user.role != Role.AGENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "IT agents only")
