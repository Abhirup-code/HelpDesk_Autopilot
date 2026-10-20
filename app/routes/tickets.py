"""Ticket endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import sla
from app.auth import require_agent
from app.models import CLOSED_STATUSES, Priority, Role, Status, Ticket, TicketType, User, utcnow
from app.schemas import StatusUpdate, TicketCreate, TicketOut


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
    )


def build_router(get_session, current_user) -> APIRouter:
    router = APIRouter(prefix="/api/tickets", tags=["tickets"])

    def visible_ticket(ticket_id: int, user: User, session: Session) -> Ticket:
        ticket = session.get(Ticket, ticket_id)
        # Return 404 (not 403) for other people's tickets so IDs can't be probed.
        if ticket is None or (user.role != Role.AGENT and ticket.requester_id != user.id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
        return ticket

    @router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
    def create_ticket(
        body: TicketCreate,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> TicketOut:
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
            due_at=sla.due_at(priority, now),
        )
        session.add(ticket)
        session.commit()
        return serialize(ticket)

    @router.get("", response_model=list[TicketOut])
    def list_tickets(
        status_filter: Status | None = Query(None, alias="status"),
        type_filter: TicketType | None = Query(None, alias="type"),
        priority: Priority | None = None,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> list[TicketOut]:
        query = select(Ticket).order_by(Ticket.due_at)
        if user.role != Role.AGENT:
            query = query.where(Ticket.requester_id == user.id)
        if status_filter:
            query = query.where(Ticket.status == status_filter)
        if type_filter:
            query = query.where(Ticket.type == type_filter)
        if priority:
            query = query.where(Ticket.priority == priority)
        return [serialize(t) for t in session.scalars(query)]

    @router.get("/{ticket_id}", response_model=TicketOut)
    def get_ticket(
        ticket_id: int,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> TicketOut:
        return serialize(visible_ticket(ticket_id, user, session))

    @router.patch("/{ticket_id}/status", response_model=TicketOut)
    def update_status(
        ticket_id: int,
        body: StatusUpdate,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> TicketOut:
        require_agent(user)
        ticket = visible_ticket(ticket_id, user, session)
        ticket.status = body.status
        ticket.resolved_at = utcnow() if body.status in CLOSED_STATUSES else None
        session.commit()
        return serialize(ticket)

    return router
