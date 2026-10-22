"""Database models."""

import enum
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import DDL, JSON, DateTime, Enum, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def str_enum(enum_class: type[enum.Enum]) -> Enum:
    """Store the readable value ("auto_resolved"), not the Python name ("AUTO_RESOLVED"),
    so SQL reports and dashboards match what the API shows."""
    return Enum(
        enum_class,
        native_enum=False,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


class Role(enum.StrEnum):
    EMPLOYEE = "employee"
    MANAGER = "manager"
    AGENT = "agent"


class TicketType(enum.StrEnum):
    PASSWORD_RESET = "password_reset"  # noqa: S105 (ticket type, not a password)
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
    REJECTED = "rejected"


CLOSED_STATUSES = {Status.RESOLVED, Status.AUTO_RESOLVED, Status.REJECTED}

# While a ticket waits for its manager, IT can't act, so the SLA clock stops.
PAUSED_STATUSES = {Status.WAITING_APPROVAL}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    department: Mapped[str] = mapped_column(String(64))
    role: Mapped[Role] = mapped_column(str_enum(Role), default=Role.EMPLOYEE)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[TicketType] = mapped_column(str_enum(TicketType))
    priority: Mapped[Priority] = mapped_column(str_enum(Priority))
    status: Mapped[Status] = mapped_column(str_enum(Status), default=Status.NEW)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Type-specific fields, e.g. new hire name and start date. See app/schemas.py.
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    # Set when an automation fails, so IT can see why the ticket came to them.
    automation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set while the SLA clock is paused (waiting for a manager).
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requester: Mapped[User] = relationship()

    def is_breached(self, now: datetime | None = None) -> bool:
        """A ticket breaches its SLA if it was resolved late, or is still open past due."""
        due = aware(self.due_at)
        if self.resolved_at is not None:
            return aware(self.resolved_at) > due
        if self.paused_at is not None:
            # The clock stopped when the pause began.
            return aware(self.paused_at) > due
        return (now or utcnow()) > due

    def work_start(self) -> datetime:
        """When work on the ticket could begin.

        Offboarding is filed ahead of time but can only run after the last day
        ends, so resolution time is measured from then.
        """
        created = aware(self.created_at)
        if self.type == TicketType.OFFBOARDING and self.details.get("last_day"):
            last_day = date.fromisoformat(self.details["last_day"])
            day_after = datetime.combine(last_day + timedelta(days=1), time.min, tzinfo=UTC)
            return max(created, day_after)
        return created

    def set_status(self, status: "Status", now: datetime | None = None) -> None:
        """Change status and keep the SLA clock and resolved time in step."""
        now = now or utcnow()
        if self.paused_at is not None and status not in PAUSED_STATUSES:
            # Push the due time back by however long the ticket was paused.
            paused_for: timedelta = now - aware(self.paused_at)
            self.due_at = aware(self.due_at) + paused_for
            self.paused_at = None
        elif self.paused_at is None and status in PAUSED_STATUSES:
            self.paused_at = now
        self.status = status
        self.resolved_at = now if status in CLOSED_STATUSES else None


class AuditEvent(Base):
    """Append-only record of every directory change."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), index=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")


# The audit log is append-only at the database level too: any UPDATE or DELETE
# fails, even from someone with direct database access through the app's account.
event.listen(
    AuditEvent.__table__,
    "after_create",
    DDL(
        """
        CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER audit_events_no_change BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
        """
    ).execute_if(dialect="postgresql"),
)
for _op in ("UPDATE", "DELETE"):
    event.listen(
        AuditEvent.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER audit_events_no_{_op.lower()} BEFORE {_op} ON audit_events "
            "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
        ).execute_if(dialect="sqlite"),
    )


class ResetToken(Base):
    """Single-use link to set a directory password. Only the hash is stored."""

    __tablename__ = "reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64))
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InboxMessage(Base):
    """Stands in for email in the demo. Each portal user sees only their own."""

    __tablename__ = "inbox_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def aware(dt: datetime) -> datetime:
    """SQLite drops timezone info; treat stored values as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
