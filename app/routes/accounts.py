"""Password links, the demo inbox, and manual automation runs."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import services
from app.auth import require_agent
from app.automation import Automator, today_utc
from app.directory import Directory
from app.models import InboxMessage, User
from app.schemas import ErrorOut, InboxMessageOut, PasswordResetRedeem

INVALID_LINK = services.INVALID_LINK


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
        services.redeem_link(session, directory, token, body.new_password)

    @router.get("/inbox", response_model=list[InboxMessageOut])
    def my_inbox(
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> list[InboxMessage]:
        """Messages that would be emailed to you. Demo stand-in for email."""
        return services.inbox(session, user)

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
