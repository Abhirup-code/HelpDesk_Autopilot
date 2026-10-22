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
from app.policy import DEPARTMENT_GROUPS, NEW_HIRE_LINK_TTL, PRIVILEGED_GROUPS, RESET_LINK_TTL

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


def issue_password_link(
    session: Session, username: str, base_url: str, ttl: timedelta, ticket: Ticket | None = None
) -> str:
    """Create a single-use link to set a directory password. Only its hash is stored."""
    token = secrets.token_urlsafe(32)
    session.add(
        ResetToken(
            token_hash=hash_token(token),
            username=username,
            ticket_id=ticket.id if ticket else None,
            expires_at=utcnow() + ttl,
        )
    )
    session.add(
        AuditEvent(
            actor=ACTOR,
            action="password_link.issue",
            target=username,
            ticket_id=ticket.id if ticket else None,
            detail=f"valid for {describe(ttl)}",
        )
    )
    return f"{base_url.rstrip('/')}/reset/{token}"


class Automator:
    def __init__(self, session: Session, directory: Directory, base_url: str) -> None:
        self.session = session
        self.directory = directory
        self.base_url = base_url.rstrip("/")

    # -- helpers ------------------------------------------------------------

    def audit(
        self,
        action: str,
        target: str,
        ticket: Ticket | None,
        detail: str = "",
        actor: str = ACTOR,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor=actor,
                action=action,
                target=target,
                ticket_id=ticket.id if ticket else None,
                detail=detail,
            )
        )

    def issue_link(self, username: str, ticket: Ticket | None, ttl: timedelta) -> str:
        return issue_password_link(self.session, username, self.base_url, ttl, ticket)

    def notify(self, user: User, subject: str, body: str) -> None:
        self.session.add(InboxMessage(user_id=user.id, subject=subject, body=body))

    def resolve(self, ticket: Ticket, status: Status = Status.AUTO_RESOLVED) -> None:
        ticket.set_status(status)
        ticket.automation_error = None

    def fail(self, ticket: Ticket, error: Exception) -> None:
        ticket.set_status(Status.IN_PROGRESS)
        ticket.automation_error = str(error)
        self.audit("automation.failed", ticket.type.value, ticket, str(error))

    # -- entry points -------------------------------------------------------

    def handle_new_ticket(self, ticket: Ticket, today: date) -> None:
        handlers = {
            TicketType.PASSWORD_RESET: self.password_reset,
            TicketType.NEW_HIRE: self.new_hire,
            TicketType.ACCESS_REQUEST: self.access_request,
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
            f"Open this link within 15 minutes to set a new password. It works once.\n\n{link}",
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

    def access_request(self, ticket: Ticket) -> None:
        AccessFlow(self).start(ticket)

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


# -- access requests and approvals ---------------------------------------------


class AccessFlow:
    """Approval workflow for access requests.

    Employee asks for a group -> their manager approves -> privileged groups
    also need an IT agent -> the group is added. Anyone in the chain can reject.
    """

    def __init__(self, automator: "Automator") -> None:
        self.a = automator

    def start(self, ticket: Ticket) -> None:
        user, group = ticket.requester, ticket.details["group"]
        if not self.a.directory.user_exists(user.username):
            raise DirectoryError(f"No directory account for {user.username}")
        if group in self.a.directory.groups_of(user.username):
            self.a.notify(user, f"You already have {group}", "No change was needed.")
            self.a.resolve(ticket)
            return
        if user.manager_id is None:
            # No manager on file, so IT decides.
            ticket.set_status(Status.WAITING_IT_APPROVAL)
            return
        ticket.set_status(Status.WAITING_APPROVAL)
        manager = self.a.session.get(User, user.manager_id)
        self.a.notify(
            manager,
            f"Approval needed: {user.full_name} wants {group}",
            f"Ticket #{ticket.id}: {ticket.description}\n\nApprove or reject at /api/approvals.",
        )

    def approve(self, ticket: Ticket, approver: User) -> None:
        group = ticket.details["group"]
        stage = "manager" if ticket.status == Status.WAITING_APPROVAL else "it"
        self.a.audit(
            f"approval.{stage}", ticket.requester.username, ticket, group, actor=approver.username
        )
        ticket.details = {**ticket.details, f"{stage}_approved_by": approver.username}
        if stage == "manager" and group in PRIVILEGED_GROUPS:
            ticket.set_status(Status.WAITING_IT_APPROVAL)
            self.a.notify(
                ticket.requester,
                f"Manager approved {group}",
                f"{group} is a privileged group, so IT must approve it as well.",
            )
            return
        self.a._run(self.grant, ticket)

    def grant(self, ticket: Ticket) -> None:
        user, group = ticket.requester, ticket.details["group"]
        self.a.directory.add_to_group(user.username, group)
        self.a.audit("group.add", user.username, ticket, group)
        self.a.notify(user, f"Access granted: {group}", f"You were added to {group}.")
        self.a.resolve(ticket, Status.RESOLVED)

    def reject(self, ticket: Ticket, approver: User, reason: str) -> None:
        group = ticket.details["group"]
        self.a.audit(
            "approval.reject",
            ticket.requester.username,
            ticket,
            f"{group}: {reason}",
            actor=approver.username,
        )
        ticket.details = {
            **ticket.details,
            "rejected_by": approver.username,
            "rejection_reason": reason,
        }
        ticket.set_status(Status.REJECTED)
        self.a.notify(
            ticket.requester,
            f"Access request rejected: {group}",
            f"{approver.full_name} rejected your request. Reason: {reason}",
        )


def today_utc() -> date:
    return utcnow().date()
