"""Password links, the demo inbox, and manual automation runs."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_token, require_agent
from app.automation import Automator, today_utc
from app.directory import Directory, DirectoryError
from app.models import AuditEvent, InboxMessage, ResetToken, User, aware, utcnow
from app.schemas import ErrorOut, InboxMessageOut, PasswordResetRedeem

INVALID_LINK = "This link is invalid, expired or already used"


def build_router(get_session, current_user, directory: Directory, base_url: str) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["accounts"])

    @router.post(
        "/password-resets/{token}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={400: {"model": ErrorOut, "description": INVALID_LINK}},
    )
    def redeem_password_link(
        token: str,
        body: PasswordResetRedeem,
        session: Session = Depends(get_session),
    ) -> None:
        """Set a directory password with a one-time link. No sign-in needed."""
        record = session.scalar(
            select(ResetToken).where(ResetToken.token_hash == hash_token(token))
        )
        # One message for every failure, so the link can't be probed.
        if record is None or record.used_at is not None or aware(record.expires_at) < utcnow():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_LINK)
        if record.username.lower() in body.new_password.lower():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Password must not contain your username"
            )
        try:
            directory.set_password(record.username, body.new_password)
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

    @router.get("/inbox", response_model=list[InboxMessageOut])
    def my_inbox(
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> list[InboxMessage]:
        """Messages that would be emailed to you. Demo stand-in for email."""
        query = (
            select(InboxMessage)
            .where(InboxMessage.user_id == user.id)
            .order_by(InboxMessage.id.desc())
        )
        return list(session.scalars(query))

    @router.post(
        "/automation/run-due",
        responses={403: {"model": ErrorOut, "description": "IT agents only"}},
    )
    def run_due(
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> dict[str, int]:
        """Run offboardings whose last day has ended. The worker does this every minute."""
        require_agent(user)
        count = Automator(session, directory, base_url).run_due_offboardings(today_utc())
        return {"offboardings_processed": count}

    return router
