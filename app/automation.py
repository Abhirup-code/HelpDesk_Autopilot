"""Automations that resolve tickets against the directory.

Every directory change is written to the audit log in the same transaction as
the ticket update. If an automation fails part way, completed steps are undone
where possible, the ticket goes to IT with the error, and the failure is audited.
"""

import secrets
import unicodedata
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_token
from app.directory import Directory, DirectoryError
from app.models import (
    AuditEvent,
    InboxMessage,
    ResetToken,
    Status,
    Ticket,
    TicketType,
    User,
    utcnow,
)
from app.policy import DEPARTMENT_GROUPS, NEW_HIRE_LINK_TTL, RESET_LINK_TTL

ACTOR = "autopilot"


def make_username(first: str, last: str, taken) -> str:
    """First initial + last name, letters only, made unique with a number."""

    def letters(text: str) -> str:
        plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        return "".join(ch for ch in plain.lower() if ch.isalpha())

    base = (letters(first)[:1] + letters(last))[:20] or "user"
    candidate, n = base, 2
    while taken(candidate):
        candidate, n = f"{base}{n}", n + 1
    return candidate


def describe(ttl: timedelta) -> str:
    minutes = int(ttl.total_seconds() // 60)
    return f"{minutes} minutes" if minutes < 120 else f"{minutes // 60} hours"


class Automator:
    def __init__(self, session: Session, directory: Directory, base_url: str) -> None:
        self.session = session
        self.directory = directory
        self.base_url = base_url.rstrip("/")

    # -- helpers ------------------------------------------------------------

    def audit(self, action: str, target: str, ticket: Ticket | None, detail: str = "") -> None:
        self.session.add(
            AuditEvent(
                actor=ACTOR,
                action=action,
                target=target,
                ticket_id=ticket.id if ticket else None,
                detail=detail,
            )
        )

    def issue_link(self, username: str, ticket: Ticket, ttl: timedelta) -> str:
        token = secrets.token_urlsafe(32)
        self.session.add(
            ResetToken(
                token_hash=hash_token(token),
                username=username,
                ticket_id=ticket.id,
                expires_at=utcnow() + ttl,
            )
        )
        self.audit("password_link.issue", username, ticket, f"valid for {describe(ttl)}")
        return f"{self.base_url}/api/password-resets/{token}"

    def notify(self, user: User, subject: str, body: str) -> None:
        self.session.add(InboxMessage(user_id=user.id, subject=subject, body=body))

    def resolve(self, ticket: Ticket) -> None:
        ticket.status = Status.AUTO_RESOLVED
        ticket.resolved_at = utcnow()
        ticket.automation_error = None

    def fail(self, ticket: Ticket, error: Exception) -> None:
        ticket.status = Status.IN_PROGRESS
        ticket.automation_error = str(error)
        self.audit("automation.failed", ticket.type.value, ticket, str(error))

    # -- entry points -------------------------------------------------------

    def handle_new_ticket(self, ticket: Ticket, today: date) -> None:
        handlers = {
            TicketType.PASSWORD_RESET: self.password_reset,
            TicketType.NEW_HIRE: self.new_hire,
        }
        if ticket.type in handlers:
            self._run(handlers[ticket.type], ticket)
        elif ticket.type == TicketType.OFFBOARDING and self._offboarding_due(ticket, today):
            self._run(self.offboard, ticket)
        self.session.commit()

    def run_due_offboardings(self, today: date) -> int:
        """Process offboardings whose last day has ended. Returns how many ran."""
        pending = self.session.scalars(
            select(Ticket).where(Ticket.type == TicketType.OFFBOARDING, Ticket.status == Status.NEW)
        ).all()
        due = [t for t in pending if self._offboarding_due(t, today)]
        for ticket in due:
            self._run(self.offboard, ticket)
        self.session.commit()
        return len(due)

    @staticmethod
    def _offboarding_due(ticket: Ticket, today: date) -> bool:
        return date.fromisoformat(ticket.details["last_day"]) < today

    def _run(self, handler, ticket: Ticket) -> None:
        try:
            handler(ticket)
        except DirectoryError as exc:
            self.fail(ticket, exc)

    # -- automations --------------------------------------------------------

    def password_reset(self, ticket: Ticket) -> None:
        user = ticket.requester
        if not self.directory.user_exists(user.username):
            raise DirectoryError(f"No directory account for {user.username}")
        link = self.issue_link(user.username, ticket, RESET_LINK_TTL)
        self.notify(
            user,
            "Your password reset link",
            f"Use this link within 15 minutes to set a new password. It works once.\n\n"
            f"POST your new password to:\n{link}",
        )
        self.resolve(ticket)

    def new_hire(self, ticket: Ticket) -> None:
        d = ticket.details
        username = make_username(d["first_name"], d["last_name"], self.directory.user_exists)
        groups = DEPARTMENT_GROUPS[d["department"]]
        created = False
        try:
            self.directory.create_user(username, d["first_name"], d["last_name"], d["department"])
            created = True
            self.audit("user.create", username, ticket, f"department={d['department']}")
            for group in groups:
                self.directory.add_to_group(username, group)
                self.audit("group.add", username, ticket, group)
        except DirectoryError:
            if created:
                self.directory.delete_user(username)
                self.audit("user.rollback", username, ticket, "removed partly created account")
            raise
        link = self.issue_link(username, ticket, NEW_HIRE_LINK_TTL)
        self.notify(
            ticket.requester,
            f"New hire ready: {d['first_name']} {d['last_name']}",
            f"Username: {username}\nGroups: {', '.join(groups)}\nStart date: {d['start_date']}\n\n"
            f"Give this one-time link to the new hire to set their password "
            f"(valid 72 hours):\n{link}",
        )
        ticket.details = {**d, "username": username}
        self.resolve(ticket)

    def offboard(self, ticket: Ticket) -> None:
        username = ticket.details["username"]
        if self.directory.is_disabled(username):
            raise DirectoryError(f"{username} is already disabled")
        removed = self.directory.groups_of(username)
        for group in removed:
            self.directory.remove_from_group(username, group)
            self.audit("group.remove", username, ticket, group)
        self.directory.disable_user(username)
        self.audit("user.disable", username, ticket, f"groups removed: {', '.join(removed)}")
        self.notify(
            ticket.requester,
            f"Offboarding complete: {username}",
            f"{username} was disabled and removed from {len(removed)} group(s): "
            f"{', '.join(removed) or 'none'}.",
        )
        self.resolve(ticket)


def today_utc() -> date:
    return utcnow().date()
