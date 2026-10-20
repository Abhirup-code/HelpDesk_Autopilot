"""Database models."""

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Role(enum.StrEnum):
    EMPLOYEE = "employee"
    MANAGER = "manager"
    AGENT = "agent"


class TicketType(enum.StrEnum):
    PASSWORD_RESET = "password_reset"
    NEW_HIRE = "new_hire"
    OFFBOARDING = "offboarding"
    ACCESS_REQUEST = "access_request"
    HARDWARE = "hardware"


class Priority(enum.StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class Status(enum.StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_IT_APPROVAL = "waiting_it_approval"
    RESOLVED = "resolved"
    AUTO_RESOLVED = "auto_resolved"


CLOSED_STATUSES = {Status.RESOLVED, Status.AUTO_RESOLVED}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    department: Mapped[str] = mapped_column(String(64))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), default=Role.EMPLOYEE)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[TicketType] = mapped_column(Enum(TicketType, native_enum=False))
    priority: Mapped[Priority] = mapped_column(Enum(Priority, native_enum=False))
    status: Mapped[Status] = mapped_column(Enum(Status, native_enum=False), default=Status.NEW)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requester: Mapped[User] = relationship()

    def is_breached(self, now: datetime | None = None) -> bool:
        """A ticket breaches its SLA if it was resolved late, or is still open past due."""
        due = _aware(self.due_at)
        if self.resolved_at is not None:
            return _aware(self.resolved_at) > due
        return (now or utcnow()) > due


def _aware(dt: datetime) -> datetime:
    # SQLite drops timezone info; treat stored values as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
