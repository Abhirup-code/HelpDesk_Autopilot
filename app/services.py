"""Business actions shared by the JSON API and the web portal.

Each function checks permissions and raises HTTPException with the same status
codes in both places, so the rules can't drift apart between the two front ends.
"""

import csv
import io
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app import sla
from app.auth import hash_token
from app.automation import AccessFlow, Automator, today_utc
from app.directory import Directory, DirectoryError
from app.models import (
    CLOSED_STATUSES,
    AuditEvent,
    InboxMessage,
    Priority,
    ResetToken,
    Role,
    Status,
    Ticket,
    TicketComment,
    TicketType,
    User,
    aware,
    utcnow,
)
from app.schemas import TicketCreate

INVALID_LINK = "This link is invalid, expired or already used"
NO_PENDING = "No pending approval for you on this ticket"
# The statuses an IT agent may set by hand. The rest are set by approvals and automations.
MANUAL_STATUSES = {Status.NEW, Status.IN_PROGRESS, Status.RESOLVED}
# Only managers and IT can request account changes for other people.
PEOPLE_CHANGES = {TicketType.NEW_HIRE, TicketType.OFFBOARDING}


def visible_ticket(session: Session, user: User, ticket_id: int) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    # 404 (not 403) for other people's tickets, so IDs can't be probed.
    if ticket is None or (user.role != Role.AGENT and ticket.requester_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket


def ticket_query(
    user: User,
    status_filter: Status | None = None,
    type_filter: TicketType | None = None,
    priority=None,
    search: str | None = None,
    overdue: bool = False,
    assignee: str | None = None,
):
    """The tickets this person may see, narrowed by the filters."""
    query = select(Ticket)
    if user.role != Role.AGENT:
        query = query.where(Ticket.requester_id == user.id)
    if status_filter:
        query = query.where(Ticket.status == status_filter)
    if type_filter:
        query = query.where(Ticket.type == type_filter)
    if priority:
        query = query.where(Ticket.priority == priority)
    if search and search.strip():
        # Matches part of the title or description and ignores capitals. Escaped, so
        # "100%" looks for those characters and not for "100" followed by anything.
        words = search.strip()
        query = query.where(
            or_(
                Ticket.title.icontains(words, autoescape=True),
                Ticket.description.icontains(words, autoescape=True),
            )
        )
    if assignee == UNASSIGNED:
        query = query.where(Ticket.assignee_id.is_(None))
    elif assignee:
        query = query.where(
            Ticket.assignee_id == select(User.id).where(User.username == assignee).scalar_subquery()
        )
    if overdue:
        # Still open and past due. A paused ticket is judged at the moment it was paused,
        # the same rule Ticket.is_breached uses.
        query = query.where(
            Ticket.status.not_in(CLOSED_STATUSES),
            or_(
                and_(Ticket.paused_at.is_(None), Ticket.due_at < utcnow()),
                and_(Ticket.paused_at.is_not(None), Ticket.paused_at > Ticket.due_at),
            ),
        )
    return query


def list_tickets(
    session: Session,
    user: User,
    status_filter: Status | None = None,
    type_filter: TicketType | None = None,
    priority=None,
    search: str | None = None,
    overdue: bool = False,
    assignee: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Ticket]:
    query = ticket_query(user, status_filter, type_filter, priority, search, overdue, assignee)
    # The id breaks ties, so pages never repeat or skip a ticket with the same due time.
    query = query.order_by(Ticket.due_at, Ticket.id).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))


def count_tickets(session: Session, user: User, *filters, **named) -> int:
    """How many tickets match, across all pages. Takes the same filters as list_tickets."""
    matching = ticket_query(user, *filters, **named).subquery()
    return session.scalar(select(func.count()).select_from(matching)) or 0


def on_team(session: Session, manager: User, username: str) -> bool:
    """True if the account belongs to someone who reports to this manager, or was
    created by one of the manager's own new hire requests."""
    person = session.scalar(select(User).where(User.username == username))
    if person is not None:
        return person.manager_id == manager.id
    hires = session.scalars(
        select(Ticket).where(Ticket.type == TicketType.NEW_HIRE, Ticket.requester_id == manager.id)
    )
    return any((t.details or {}).get("username") == username for t in hires)


def create_ticket(
    session: Session, directory: Directory, base_url: str, user: User, body: TicketCreate
) -> Ticket:
    if body.type in PEOPLE_CHANGES and user.role == Role.EMPLOYEE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only managers and IT can request this")
    if body.type == TicketType.OFFBOARDING:
        username = body.details["username"]
        if not directory.user_exists(username):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"No directory account named {username}"
            )
        if user.role == Role.MANAGER and not on_team(session, user, username):
            # Without this, any manager could switch off any account in the company.
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "You can only offboard people on your own team. Ask IT for anyone else.",
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
    if new_status not in MANUAL_STATUSES:
        # The other statuses mean something happened: an approval, a rejection with a
        # reason, or an automation. Setting them by hand would make the record lie.
        allowed = ", ".join(s.value for s in Status if s in MANUAL_STATUSES)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Status can only be set by hand to: {allowed}",
        )
    old_status = ticket.status
    if new_status == old_status:
        return ticket
    ticket.set_status(new_status)
    session.add(
        AuditEvent(
            actor=user.username,
            action="ticket.status",
            target=ticket.requester.username,
            ticket_id=ticket.id,
            detail=f"{old_status.value} -> {new_status.value}",
        )
    )
    session.commit()
    return ticket


def list_comments(session: Session, user: User, ticket_id: int) -> list[TicketComment]:
    """Comments on a ticket, oldest first. Requesters never see internal notes."""
    visible_ticket(session, user, ticket_id)
    query = select(TicketComment).where(TicketComment.ticket_id == ticket_id)
    if user.role != Role.AGENT:
        query = query.where(TicketComment.internal.is_(False))
    return list(session.scalars(query.order_by(TicketComment.created_at, TicketComment.id)))


def add_comment(
    session: Session, user: User, ticket_id: int, body: str, internal: bool = False
) -> TicketComment:
    """Add a reply to a ticket. Only IT agents may write an internal note."""
    ticket = visible_ticket(session, user, ticket_id)
    if internal and user.role != Role.AGENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only IT agents can write internal notes")
    text = body.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Comment must not be blank")
    now = utcnow()
    comment = TicketComment(
        ticket_id=ticket.id, author_id=user.id, body=text, internal=internal, created_at=now
    )
    session.add(comment)
    # First reply from anyone but the requester. An internal note is not a reply:
    # the person waiting has still heard nothing.
    if not internal and ticket.requester_id != user.id and ticket.first_response_at is None:
        ticket.first_response_at = now
    # A reply to someone else's ticket is worth an email, and the requester cannot see
    # internal notes, so those raise nothing.
    if not internal and ticket.requester_id != user.id:
        session.add(
            InboxMessage(
                user_id=ticket.requester_id,
                subject=f"Reply on your ticket #{ticket.id}: {ticket.title}",
                body=f"{user.full_name} wrote:\n\n{text}",
            )
        )
    session.commit()
    return comment


UNASSIGNED = "unassigned"
# How long after a ticket closes it can still be reopened. After that the problem
# is a new one, with its own SLA, rather than the old ticket dragged back to life.
REOPEN_WINDOW = timedelta(days=7)
# A rejected request was a decision, not a fix. Ask again with a new ticket.
REOPENABLE = {Status.RESOLVED, Status.AUTO_RESOLVED}


def reopen_ticket(session: Session, user: User, ticket_id: int, reason: str) -> Ticket:
    """Put a closed ticket back in the queue because the fix did not hold."""
    ticket = visible_ticket(session, user, ticket_id)
    if ticket.status not in REOPENABLE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Only a resolved ticket can be reopened. Raise a new request instead.",
        )
    closed_for = utcnow() - aware(ticket.resolved_at)
    if closed_for > REOPEN_WINDOW:
        days = REOPEN_WINDOW.days
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"This ticket closed more than {days} days ago. Raise a new request instead.",
        )
    text = reason.strip()
    if not text:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Say what is still wrong when you reopen"
        )
    now = utcnow()
    ticket.reopen_count += 1
    ticket.set_status(Status.IN_PROGRESS, now)
    # A fresh clock from now: the old due time is long past and judging the second
    # attempt by the first one's deadline would mark it late before work restarts.
    ticket.due_at = sla.due_at(ticket.priority, now)
    session.add(
        TicketComment(
            ticket_id=ticket.id,
            author_id=user.id,
            body=f"Reopened: {text}",
            internal=False,
            created_at=now,
        )
    )
    session.add(
        AuditEvent(
            actor=user.username,
            action="ticket.reopen",
            target=ticket.requester.username,
            ticket_id=ticket.id,
            detail=f"reopen #{ticket.reopen_count}: {text[:200]}",
        )
    )
    if ticket.assignee is not None and ticket.assignee_id != user.id:
        session.add(
            InboxMessage(
                user_id=ticket.assignee_id,
                subject=f"Ticket #{ticket.id} is back: {ticket.title}",
                body=f"{user.full_name} reopened this ticket.\n\n{text}",
            )
        )
    session.commit()
    return ticket


def agents(session: Session) -> list[User]:
    """Everyone who can own a ticket, by name."""
    return list(
        session.scalars(select(User).where(User.role == Role.AGENT).order_by(User.username))
    )


def assign_ticket(session: Session, user: User, ticket_id: int, username: str | None) -> Ticket:
    """Give a ticket to an IT agent, or put it back in the unassigned queue."""
    if user.role != Role.AGENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "IT agents only")
    ticket = visible_ticket(session, user, ticket_id)
    assignee = None
    if username:
        assignee = session.scalar(select(User).where(User.username == username))
        if assignee is None or assignee.role != Role.AGENT:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{username} is not an IT agent"
            )
    before = ticket.assignee.username if ticket.assignee else UNASSIGNED
    after = assignee.username if assignee else UNASSIGNED
    if before == after:
        return ticket
    # Set the object, not just the id: sessions here keep objects alive after commit,
    # so an id alone would leave a stale ticket.assignee behind.
    ticket.assignee = assignee
    session.add(
        AuditEvent(
            actor=user.username,
            action="ticket.assign",
            target=ticket.requester.username,
            ticket_id=ticket.id,
            detail=f"{before} -> {after}",
        )
    )
    # Tell the new owner, unless they just picked it up themselves.
    if assignee is not None and assignee.id != user.id:
        session.add(
            InboxMessage(
                user_id=assignee.id,
                subject=f"Ticket #{ticket.id} is yours: {ticket.title}",
                body=f"{user.full_name} assigned this ticket to you.",
            )
        )
    session.commit()
    return ticket


RATEABLE = {Status.RESOLVED, Status.AUTO_RESOLVED}


def rate_ticket(
    session: Session, user: User, ticket_id: int, score: int, comment: str | None = None
) -> Ticket:
    """Record what the requester thought of the fix. Only they can say, and only once."""
    ticket = visible_ticket(session, user, ticket_id)
    if ticket.requester_id != user.id:
        # An agent rating their own work would make the number meaningless.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the person who raised the ticket can rate it"
        )
    if ticket.status not in RATEABLE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "You can rate a ticket once it is resolved"
        )
    if ticket.satisfaction is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already rated this ticket")
    ticket.satisfaction = score
    ticket.satisfaction_comment = (comment or "").strip() or None
    ticket.satisfaction_at = utcnow()
    session.add(
        AuditEvent(
            actor=user.username,
            action="ticket.rated",
            target=ticket.requester.username,
            ticket_id=ticket.id,
            detail=f"{score} out of 5",
        )
    )
    session.commit()
    return ticket


def change_priority(session: Session, user: User, ticket_id: int, new: Priority) -> Ticket:
    """Raise or lower a ticket's priority, moving its due time to match."""
    if user.role != Role.AGENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "IT agents only")
    ticket = visible_ticket(session, user, ticket_id)
    if ticket.status in CLOSED_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "This ticket is closed, so its priority is final"
        )
    old = ticket.priority
    if new == old:
        return ticket
    created = aware(ticket.created_at)
    details = ticket.details or {}
    # Move the due time by the difference between the two targets, rather than
    # recalculating it from scratch. That keeps any time the clock spent paused,
    # and keeps the date rules (a new hire's start date) in charge where they apply.
    was = sla.ticket_due_at(ticket.type, old, created, details)
    now_due = sla.ticket_due_at(ticket.type, new, created, details)
    ticket.priority = new
    ticket.due_at = aware(ticket.due_at) + (now_due - was)
    session.add(
        AuditEvent(
            actor=user.username,
            action="ticket.priority",
            target=ticket.requester.username,
            ticket_id=ticket.id,
            detail=f"{old.value} -> {new.value}",
        )
    )
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


AUDIT_CSV_COLUMNS = ["when_utc", "who", "action", "user", "ticket", "detail"]
# A cell that starts with one of these runs as a formula when the file is opened in
# Excel or Google Sheets. Audit text comes from people (ticket reasons, usernames).
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(FORMULA_STARTS) else text


TICKET_CSV_COLUMNS = [
    "id",
    "type",
    "priority",
    "status",
    "title",
    "requester",
    "assignee",
    "created_utc",
    "due_utc",
    "resolved_utc",
    "sla_breached",
    "first_response_seconds",
    "resolution_seconds",
    "reopen_count",
    "satisfaction",
]


def stamp(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else ""


def ticket_csv(tickets: list[Ticket]) -> str:
    """The queue as CSV, for the weekly report nobody wants to retype."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(TICKET_CSV_COLUMNS)
    for t in tickets:
        resolved = aware(t.resolved_at) if t.resolved_at else None
        row = [
            t.id,
            t.type.value,
            t.priority.value,
            t.status.value,
            t.title,
            t.requester.username,
            t.assignee.username if t.assignee else "",
            stamp(aware(t.created_at)),
            stamp(aware(t.due_at)),
            stamp(resolved),
            "yes" if t.is_breached() else "no",
            t.first_response_seconds(),
            int((resolved - t.work_start()).total_seconds()) if resolved else None,
            t.reopen_count,
            t.satisfaction,
        ]
        writer.writerow([csv_cell(value) for value in row])
    return out.getvalue()


def audit_csv(events: list[AuditEvent]) -> str:
    """The audit log as CSV for auditors: one row per entry, newest first."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(AUDIT_CSV_COLUMNS)
    for e in events:
        when = stamp(aware(e.at))
        row = [when, e.actor, e.action, e.target, e.ticket_id, e.detail]
        writer.writerow([csv_cell(value) for value in row])
    return out.getvalue()
