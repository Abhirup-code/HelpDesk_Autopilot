"""Ticket endpoints."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app import services
from app.directory import Directory
from app.models import Priority, Status, Ticket, TicketType, User
from app.schemas import ErrorOut, StatusUpdate, TicketCreate, TicketOut

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
        details=ticket.details or {},
        automation_error=ticket.automation_error,
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
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> list[TicketOut]:
        tickets = services.list_tickets(session, user, status_filter, type_filter, priority)
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

    return router
