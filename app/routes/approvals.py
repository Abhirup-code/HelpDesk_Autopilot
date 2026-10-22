"""Approvals for access requests, and the read-only audit log."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import services
from app.directory import Directory
from app.models import AuditEvent, User
from app.routes.tickets import serialize
from app.schemas import ApprovalDecision, AuditEventOut, ErrorOut, TicketOut


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
        return [serialize(t) for t in services.pending_approvals(session, user)]

    @router.post(
        "/approvals/{ticket_id}",
        response_model=TicketOut,
        responses={
            403: {"model": ErrorOut, "description": "You can't approve your own request"},
            404: {"model": ErrorOut, "description": services.NO_PENDING},
        },
    )
    def decide(
        ticket_id: int,
        body: ApprovalDecision,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> TicketOut:
        ticket = services.decide_approval(
            session, directory, base_url, user, ticket_id, body.decision, body.reason
        )
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
        return services.audit_log(session, user, target, action, ticket_id, limit)

    return router
