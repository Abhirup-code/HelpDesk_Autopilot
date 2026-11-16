"""Ticket endpoints."""

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app import services
from app.directory import Directory
from app.models import Priority, Status, Ticket, TicketComment, TicketType, User
from app.schemas import (
    AssigneeUpdate,
    CommentCreate,
    CommentOut,
    ErrorOut,
    ReopenRequest,
    StatusUpdate,
    TicketCreate,
    TicketOut,
)

UNAUTHORIZED = {401: {"model": ErrorOut, "description": "Missing or invalid token"}}
FORBIDDEN = {403: {"model": ErrorOut, "description": "Your role cannot do this"}}
NOT_FOUND = {404: {"model": ErrorOut, "description": "Ticket not found or not yours"}}


def serialize(ticket: Ticket) -> TicketOut:
    return TicketOut(
        id=ticket.id,
        type=ticket.type,
        priority=ticket.priority,
        status=ticket.status,
        title=ticket.title,
        description=ticket.description,
        requester_id=ticket.requester_id,
        created_at=ticket.created_at,
        due_at=ticket.due_at,
        resolved_at=ticket.resolved_at,
        sla_breached=ticket.is_breached(),
        sla_seconds_left=ticket.seconds_left(),
        details=ticket.details or {},
        automation_error=ticket.automation_error,
        assignee=ticket.assignee.username if ticket.assignee else None,
        reopen_count=ticket.reopen_count,
        first_response_at=ticket.first_response_at,
        first_response_seconds=ticket.first_response_seconds(),
    )


def serialize_comment(comment: TicketComment) -> CommentOut:
    return CommentOut(
        id=comment.id,
        ticket_id=comment.ticket_id,
        author=comment.author.username,
        body=comment.body,
        internal=comment.internal,
        created_at=comment.created_at,
    )


def build_router(get_session, current_user, directory: Directory, base_url: str) -> APIRouter:
    router = APIRouter(prefix="/api/tickets", tags=["tickets"], responses=UNAUTHORIZED)

    @router.post(
        "",
        response_model=TicketOut,
        status_code=status.HTTP_201_CREATED,
        responses={**FORBIDDEN},
    )
    def create_ticket(
        body: TicketCreate,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> TicketOut:
        return serialize(services.create_ticket(session, directory, base_url, user, body))

    @router.get("", response_model=list[TicketOut])
    def list_tickets(
        status_filter: Status | None = Query(None, alias="status"),
        type_filter: TicketType | None = Query(None, alias="type"),
        priority: Priority | None = None,
        q: str | None = Query(
            None, max_length=100, description="Words in the title or description"
        ),
        overdue: bool = Query(False, description="Only open tickets that are past due"),
        assignee: str | None = Query(
            None, max_length=64, description="An agent's username, or 'unassigned'"
        ),
        limit: int = Query(100, ge=1, le=500, description="Page size"),
        offset: int = Query(0, ge=0, description="How many tickets to skip"),
        *,
        response: Response,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> list[TicketOut]:
        """Most urgent first. Returns one page; the X-Total-Count header has the full count."""
        filters = (status_filter, type_filter, priority, q, overdue, assignee)
        tickets = services.list_tickets(session, user, *filters, limit=limit, offset=offset)
        response.headers["X-Total-Count"] = str(services.count_tickets(session, user, *filters))
        return [serialize(t) for t in tickets]

    @router.get("/{ticket_id}", response_model=TicketOut, responses={**NOT_FOUND})
    def get_ticket(
        ticket_id: int,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> TicketOut:
        return serialize(services.visible_ticket(session, user, ticket_id))

    @router.patch(
        "/{ticket_id}/status",
        response_model=TicketOut,
        responses={**FORBIDDEN, **NOT_FOUND},
    )
    def update_status(
        ticket_id: int,
        body: StatusUpdate,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> TicketOut:
        return serialize(services.change_status(session, user, ticket_id, body.status))

    @router.post(
        "/{ticket_id}/reopen",
        response_model=TicketOut,
        responses={**NOT_FOUND},
    )
    def reopen(
        ticket_id: int,
        body: ReopenRequest,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> TicketOut:
        """Put a resolved ticket back in the queue, with a fresh SLA clock."""
        return serialize(services.reopen_ticket(session, user, ticket_id, body.reason))

    @router.patch(
        "/{ticket_id}/assignee",
        response_model=TicketOut,
        responses={**FORBIDDEN, **NOT_FOUND},
    )
    def update_assignee(
        ticket_id: int,
        body: AssigneeUpdate,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> TicketOut:
        """Give the ticket to an IT agent, or send null to put it back in the queue."""
        return serialize(services.assign_ticket(session, user, ticket_id, body.assignee))

    @router.get("/{ticket_id}/comments", response_model=list[CommentOut], responses={**NOT_FOUND})
    def list_comments(
        ticket_id: int,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> list[CommentOut]:
        """Oldest first. Internal notes are left out for anyone who is not an IT agent."""
        comments = services.list_comments(session, user, ticket_id)
        return [serialize_comment(c) for c in comments]

    @router.post(
        "/{ticket_id}/comments",
        response_model=CommentOut,
        status_code=status.HTTP_201_CREATED,
        responses={**FORBIDDEN, **NOT_FOUND},
    )
    def add_comment(
        ticket_id: int,
        body: CommentCreate,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> CommentOut:
        comment = services.add_comment(session, user, ticket_id, body.body, body.internal)
        return serialize_comment(comment)

    return router
