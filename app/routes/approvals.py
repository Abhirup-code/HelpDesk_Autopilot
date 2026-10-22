"""Approvals for access requests, and the read-only audit log."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_agent
from app.automation import AccessFlow, Automator
from app.directory import Directory
from app.models import AuditEvent, Role, Status, Ticket, User
from app.routes.tickets import serialize
from app.schemas import ApprovalDecision, AuditEventOut, ErrorOut, TicketOut

NO_PENDING = "No pending approval for you on this ticket"


def can_decide(ticket: Ticket, user: User) -> bool:
    if ticket.status == Status.WAITING_APPROVAL:
        return ticket.requester.manager_id == user.id
    if ticket.status == Status.WAITING_IT_APPROVAL:
        return user.role == Role.AGENT
    return False


def build_router(get_session, current_user, directory: Directory, base_url: str) -> APIRouter:
    router = APIRouter(
        prefix="/api",
        tags=["approvals and audit"],
        responses={401: {"model": ErrorOut, "description": "Missing or invalid token"}},
    )

    @router.get("/approvals", response_model=list[TicketOut])
    def pending_approvals(
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> list[TicketOut]:
        """Requests waiting for you: your team's requests if you are a manager,
        privileged and no-manager requests if you are an IT agent."""
        query = select(Ticket).join(Ticket.requester).order_by(Ticket.created_at)
        if user.role == Role.AGENT:
            query = query.where(Ticket.status == Status.WAITING_IT_APPROVAL)
        else:
            query = query.where(
                Ticket.status == Status.WAITING_APPROVAL, User.manager_id == user.id
            )
        return [serialize(t) for t in session.scalars(query)]

    @router.post(
        "/approvals/{ticket_id}",
        response_model=TicketOut,
        responses={
            403: {"model": ErrorOut, "description": "You can't approve your own request"},
            404: {"model": ErrorOut, "description": NO_PENDING},
        },
    )
    def decide(
        ticket_id: int,
        body: ApprovalDecision,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> TicketOut:
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
                    detail=f"tried to {body.decision} own request for {ticket.details['group']}",
                )
            )
            session.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't approve your own request")
        flow = AccessFlow(Automator(session, directory, base_url))
        if body.decision == "approve":
            flow.approve(ticket, user)
        else:
            flow.reject(ticket, user, body.reason.strip())
        session.commit()
        return serialize(ticket)

    @router.get(
        "/audit",
        response_model=list[AuditEventOut],
        responses={403: {"model": ErrorOut, "description": "IT agents only"}},
    )
    def audit_log(
        target: str | None = None,
        action: str | None = None,
        ticket_id: int | None = None,
        limit: int = Query(100, ge=1, le=500),
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> list[AuditEvent]:
        """Every directory change, newest first. Read-only: there is no way to edit
        or delete entries, and the database refuses it too."""
        require_agent(user)
        query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
        if target:
            query = query.where(AuditEvent.target == target)
        if action:
            query = query.where(AuditEvent.action == action)
        if ticket_id is not None:
            query = query.where(AuditEvent.ticket_id == ticket_id)
        return list(session.scalars(query))

    return router
