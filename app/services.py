"""Business actions shared by the JSON API and the web portal.

Each function checks permissions and raises HTTPException with the same status
codes in both places, so the rules can't drift apart between the two front ends.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import sla
from app.auth import hash_token
from app.automation import AccessFlow, Automator, today_utc
from app.directory import Directory, DirectoryError
from app.models import (
    AuditEvent,
    InboxMessage,
    ResetToken,
    Role,
    Status,
    Ticket,
    TicketType,
    User,
    aware,
    utcnow,
)
from app.schemas import TicketCreate

INVALID_LINK = "This link is invalid, expired or already used"
NO_PENDING = "No pending approval for you on this ticket"
# Only managers and IT can request account changes for other people.
PEOPLE_CHANGES = {TicketType.NEW_HIRE, TicketType.OFFBOARDING}


def visible_ticket(session: Session, user: User, ticket_id: int) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    # 404 (not 403) for other people's tickets, so IDs can't be probed.
    if ticket is None or (user.role != Role.AGENT and ticket.requester_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket


def list_tickets(
    session: Session,
    user: User,
    status_filter: Status | None = None,
    type_filter: TicketType | None = None,
    priority=None,
) -> list[Ticket]:
    query = select(Ticket).order_by(Ticket.due_at)
    if user.role != Role.AGENT:
        query = query.where(Ticket.requester_id == user.id)
    if status_filter:
        query = query.where(Ticket.status == status_filter)
    if type_filter:
        query = query.where(Ticket.type == type_filter)
    if priority:
        query = query.where(Ticket.priority == priority)
    return list(session.scalars(query))


def create_ticket(
    session: Session, directory: Directory, base_url: str, user: User, body: TicketCreate
) -> Ticket:
    if body.type in PEOPLE_CHANGES and user.role == Role.EMPLOYEE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only managers and IT can request this")
    if body.type == TicketType.OFFBOARDING and not directory.user_exists(body.details["username"]):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"No directory account named {body.details['username']}",
        )
    now = utcnow()
    priority = body.priority or sla.default_priority(body.type)
    ticket = Ticket(
        type=body.type,
        priority=priority,
        status=Status.NEW,
        title=body.title,
        description=body.description,
        requester_id=user.id,
        created_at=now,
        due_at=sla.ticket_due_at(body.type, priority, now, body.details),
        details=body.details,
    )
    session.add(ticket)
    session.commit()
    Automator(session, directory, base_url).handle_new_ticket(ticket, today_utc())
    return ticket


def change_status(session: Session, user: User, ticket_id: int, new_status: Status) -> Ticket:
    if user.role != Role.AGENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "IT agents only")
    ticket = visible_ticket(session, user, ticket_id)
    ticket.set_status(new_status)
    session.commit()
    return ticket


def can_decide(ticket: Ticket, user: User) -> bool:
    if ticket.status == Status.WAITING_APPROVAL:
        return ticket.requester.manager_id == user.id
    if ticket.status == Status.WAITING_IT_APPROVAL:
        return user.role == Role.AGENT
    return False


def pending_approvals(session: Session, user: User) -> list[Ticket]:
    query = select(Ticket).join(Ticket.requester).order_by(Ticket.created_at)
    if user.role == Role.AGENT:
        query = query.where(Ticket.status == Status.WAITING_IT_APPROVAL)
    else:
        query = query.where(Ticket.status == Status.WAITING_APPROVAL, User.manager_id == user.id)
    return list(session.scalars(query))


def decide_approval(
    session: Session,
    directory: Directory,
    base_url: str,
    user: User,
    ticket_id: int,
    decision: str,
    reason: str | None = None,
) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None or not can_decide(ticket, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_PENDING)
    if ticket.requester_id == user.id:
        # Separation of duties: nobody approves their own access. The attempt is
        # logged, because security teams want to see who tried.
        session.add(
            AuditEvent(
                actor=user.username,
                action="approval.denied",
                target=user.username,
                ticket_id=ticket.id,
                detail=f"tried to {decision} own request for {ticket.details['group']}",
            )
        )
        session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't approve your own request")
    flow = AccessFlow(Automator(session, directory, base_url))
    if decision == "approve":
        flow.approve(ticket, user)
    else:
        flow.reject(ticket, user, (reason or "").strip())
    session.commit()
    return ticket


def find_link(session: Session, token: str) -> ResetToken:
    record = session.scalar(select(ResetToken).where(ResetToken.token_hash == hash_token(token)))
    # One message for every failure, so links can't be probed.
    if record is None or record.used_at is not None or aware(record.expires_at) < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_LINK)
    return record


def redeem_link(session: Session, directory: Directory, token: str, new_password: str) -> str:
    """Set a directory password with a one-time link. Returns the username."""
    record = find_link(session, token)
    if record.username.lower() in new_password.lower():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Password must not contain your username"
        )
    try:
        directory.set_password(record.username, new_password)
    except DirectoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_LINK) from exc
    record.used_at = utcnow()
    session.add(
        AuditEvent(
            actor=record.username,
            action="password.set",
            target=record.username,
            ticket_id=record.ticket_id,
            detail="via one-time link",
        )
    )
    session.commit()
    return record.username


def inbox(session: Session, user: User) -> list[InboxMessage]:
    query = (
        select(InboxMessage).where(InboxMessage.user_id == user.id).order_by(InboxMessage.id.desc())
    )
    return list(session.scalars(query))


def audit_log(
    session: Session,
    user: User,
    target: str | None = None,
    action: str | None = None,
    ticket_id: int | None = None,
    limit: int = 100,
) -> list[AuditEvent]:
    if user.role != Role.AGENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "IT agents only")
    # Newest first by time, so entries read in order even when they were
    # imported out of sequence (like the demo data).
    query = select(AuditEvent).order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(limit)
    # Filters match part of the text and ignore capitals: "group" finds group.add
    # and group.remove, "Emma" finds emma.
    if target:
        query = query.where(AuditEvent.target.icontains(target.strip(), autoescape=True))
    if action:
        query = query.where(AuditEvent.action.icontains(action.strip(), autoescape=True))
    if ticket_id is not None:
        query = query.where(AuditEvent.ticket_id == ticket_id)
    return list(session.scalars(query))
