"""Web portal: the same help desk as the API, as pages people can click through.

Sign-in checks the person's password against the company directory. Sessions
live in a signed cookie, every form carries a CSRF token, repeated wrong
passwords lock the account for a while, and pages send a strict
Content-Security-Policy.
"""

import hmac
import os
import re
import secrets
import time
from collections import defaultdict
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app import services
from app.directory import Directory
from app.models import AuditEvent, Role, Status, TicketType, User, aware, utcnow
from app.policy import DEPARTMENTS, MIN_PASSWORD_LENGTH, REQUESTABLE_GROUPS
from app.schemas import TicketCreate

HERE = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

TYPE_LABELS = {
    "password_reset": "Password reset",
    "access_request": "Access request",
    "new_hire": "New hire",
    "offboarding": "Offboarding",
    "hardware": "Hardware or other",
}
STATUS_LABELS = {
    "new": "New",
    "in_progress": "With IT",
    "waiting_approval": "Waiting for manager",
    "waiting_it_approval": "Waiting for IT",
    "resolved": "Resolved",
    "auto_resolved": "Done automatically",
    "rejected": "Rejected",
}
URL = re.compile(r"(https?://[^\s<]+)")


def linkify(text: str) -> Markup:
    """Escape a message body and turn links into clickable anchors."""
    # Safe: the text is HTML-escaped first, so quotes and tags can't break out.
    return Markup(URL.sub(r'<a href="\1">\1</a>', str(escape(text))))  # noqa: S704


def when(dt) -> str:
    return aware(dt).strftime("%b %d, %H:%M UTC") if dt else ""


templates.env.filters.update(linkify=linkify, when=when)
templates.env.globals.update(TYPE_LABELS=TYPE_LABELS, STATUS_LABELS=STATUS_LABELS)


class LoginThrottle:
    """Locks a username after repeated wrong passwords."""

    def __init__(self) -> None:
        self.failures: dict[str, list[float]] = defaultdict(list)

    def _recent(self, username: str) -> list[float]:
        cutoff = time.monotonic() - LOCKOUT_SECONDS
        self.failures[username] = [t for t in self.failures[username] if t > cutoff]
        return self.failures[username]

    def locked(self, username: str) -> bool:
        return len(self._recent(username)) >= MAX_FAILURES

    def fail(self, username: str) -> bool:
        """Record a failure. True when this failure locks the account."""
        recent = self._recent(username)
        recent.append(time.monotonic())
        return len(recent) == MAX_FAILURES

    def reset(self, username: str) -> None:
        self.failures.pop(username, None)


# Pages people may be sent back to after signing in. The redirect is always
# rebuilt from this list, never taken from the request, so it can't point
# anywhere else.
RETURN_PAGES = ("/tickets/new", "/tickets", "/approvals", "/inbox", "/audit")
TICKET_PAGE = re.compile(r"^/tickets/(\d{1,9})$")


def safe_next(target: str | None) -> str:
    target = target or ""
    for page in RETURN_PAGES:
        if target == page:
            return page
    match = TICKET_PAGE.match(target)
    if match:
        return f"/tickets/{int(match.group(1))}"
    return "/tickets"


def install_portal(
    app: FastAPI, get_session, directory: Directory, base_url: str, grafana_url: str
) -> None:
    secret = os.environ.get("PORTAL_SECRET_KEY") or secrets.token_urlsafe(32)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="helpdesk_session",
        max_age=8 * 3600,
        same_site="lax",
        https_only=base_url.startswith("https://"),
    )
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    throttle = LoginThrottle()
    app.state.login_throttle = throttle
    router = APIRouter(include_in_schema=False)

    # -- helpers ----------------------------------------------------------------

    def csrf_token(request: Request) -> str:
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(32)
        return request.session["csrf"]

    def check_csrf(request: Request, token: str) -> None:
        expected = request.session.get("csrf", "")
        if not expected or not hmac.compare_digest(expected, token):
            raise HTTPException(400, "Your session expired. Go back, refresh and try again.")

    def render(request: Request, name: str, status_code: int = 200, **context) -> Response:
        user = context.get("user")
        context.update(
            status_code=status_code,
            csrf=csrf_token(request),
            flash=request.session.pop("flash", None),
            grafana_url=grafana_url,
            nav=nav_for(user),
        )
        response = templates.TemplateResponse(request, name, context, status_code=status_code)
        response.headers["Content-Security-Policy"] = CSP
        return response

    def nav_for(user: User | None) -> list[tuple[str, str]]:
        if user is None:
            return []
        items = [("/tickets", "Tickets"), ("/tickets/new", "New request")]
        if user.role in (Role.MANAGER, Role.AGENT):
            items.append(("/approvals", "Approvals"))
        items.append(("/inbox", "Inbox"))
        if user.role == Role.AGENT:
            items.append(("/audit", "Audit log"))
        return items

    def flash(request: Request, message: str) -> None:
        request.session["flash"] = message

    def go(path: str) -> RedirectResponse:
        return RedirectResponse(path, status_code=303)

    def signed_in(request: Request, session: Session = Depends(get_session)) -> User:
        user_id = request.session.get("user_id")
        user = session.get(User, user_id) if user_id else None
        if user is None:
            raise HTTPException(401)
        return user

    def error_page(request: Request, user: User | None, exc: HTTPException) -> Response:
        if exc.status_code == 401:
            # Only known portal pages go into the link, never raw request text.
            return go(f"/login?next={quote(safe_next(request.url.path))}")
        message = exc.detail if isinstance(exc.detail, str) else "Something went wrong."
        return render(request, "error.html", exc.status_code, user=user, message=message)

    # -- sign in and out ------------------------------------------------------------

    @router.get("/")
    def home(request: Request) -> Response:
        return go("/tickets" if request.session.get("user_id") else "/login")

    @router.get("/login")
    def login_page(request: Request, next: str | None = None) -> Response:
        return render(request, "login.html", next=safe_next(next))

    @router.post("/login")
    def login(
        request: Request,
        username: str = Form(""),
        password: str = Form(""),
        csrf: str = Form(""),
        next: str = Form("/tickets"),
        session: Session = Depends(get_session),
    ) -> Response:
        check_csrf(request, csrf)
        username = username.strip().lower()
        if throttle.locked(username):
            return render(
                request,
                "login.html",
                429,
                next=safe_next(next),
                error="Too many wrong passwords. Try again in 15 minutes or ask IT.",
            )
        user = session.scalar(select(User).where(User.username == username))
        if user is None or not directory.authenticate(username, password):
            if throttle.fail(username):
                session.add(
                    AuditEvent(
                        actor=username or "unknown",
                        action="signin.locked",
                        target=username or "unknown",
                        detail=f"{MAX_FAILURES} wrong passwords; locked for 15 minutes",
                    )
                )
                session.commit()
            # Same message whether the user exists or not.
            return render(
                request,
                "login.html",
                401,
                next=safe_next(next),
                error="Wrong username or password.",
                username=username,
            )
        throttle.reset(username)
        request.session.clear()  # new session on sign-in
        request.session["user_id"] = user.id
        return go(safe_next(next))

    @router.post("/logout")
    def logout(request: Request, csrf: str = Form("")) -> Response:
        check_csrf(request, csrf)
        request.session.clear()
        flash(request, "You signed out.")
        return go("/login")

    # -- tickets ----------------------------------------------------------------------

    @router.get("/tickets")
    def tickets_page(
        request: Request, show: str = "open", session: Session = Depends(get_session)
    ) -> Response:
        try:
            user = signed_in(request, session)
        except HTTPException as exc:
            return error_page(request, None, exc)
        tickets = services.list_tickets(session, user)
        if show == "open":
            tickets = [t for t in tickets if t.status not in services_closed()]
        return render(request, "tickets.html", user=user, tickets=tickets, show=show)

    @router.get("/tickets/new")
    def new_ticket_page(request: Request, session: Session = Depends(get_session)) -> Response:
        try:
            user = signed_in(request, session)
        except HTTPException as exc:
            return error_page(request, None, exc)
        return render(request, "new_ticket.html", user=user, **form_options(user), form={})

    @router.post("/tickets/new")
    async def create_ticket(request: Request, session: Session = Depends(get_session)) -> Response:
        try:
            user = signed_in(request, session)
        except HTTPException as exc:
            return error_page(request, None, exc)
        form = dict(await request.form())
        check_csrf(request, form.get("csrf", ""))
        try:
            body = ticket_from_form(form)
            ticket = services.create_ticket(session, directory, base_url, user, body)
        except ValidationError as exc:
            errors = [friendly(e) for e in exc.errors()]
            return render(
                request,
                "new_ticket.html",
                422,
                user=user,
                errors=errors,
                form=form,
                **form_options(user),
            )
        except HTTPException as exc:
            if exc.status_code in (403, 422):
                return render(
                    request,
                    "new_ticket.html",
                    exc.status_code,
                    user=user,
                    errors=[exc.detail],
                    form=form,
                    **form_options(user),
                )
            raise
        flash(request, f"Ticket #{ticket.id} created: {STATUS_LABELS[ticket.status.value]}.")
        return go(f"/tickets/{ticket.id}")

    @router.get("/tickets/{ticket_id}")
    def ticket_page(
        request: Request, ticket_id: int, session: Session = Depends(get_session)
    ) -> Response:
        user = None
        try:
            user = signed_in(request, session)
            ticket = services.visible_ticket(session, user, ticket_id)
        except HTTPException as exc:
            return error_page(request, user, exc)
        history = session.scalars(
            select(AuditEvent).where(AuditEvent.ticket_id == ticket.id).order_by(AuditEvent.id)
        ).all()
        return render(
            request,
            "ticket.html",
            user=user,
            ticket=ticket,
            history=history,
            breached=ticket.is_breached(),
            statuses=[s.value for s in Status],
        )

    @router.post("/tickets/{ticket_id}/status")
    def ticket_status(
        request: Request,
        ticket_id: int,
        status: str = Form(...),
        csrf: str = Form(""),
        session: Session = Depends(get_session),
    ) -> Response:
        user = None
        try:
            user = signed_in(request, session)
            check_csrf(request, csrf)
            ticket = services.change_status(session, user, ticket_id, Status(status))
        except HTTPException as exc:
            return error_page(request, user, exc)
        except ValueError:
            return error_page(request, user, HTTPException(400, "Unknown status."))
        flash(request, f"Status changed to {STATUS_LABELS[status]}.")
        # Build the address from the saved ticket, not from the request.
        return go(f"/tickets/{ticket.id}")

    # -- approvals -----------------------------------------------------------------------

    @router.get("/approvals")
    def approvals_page(request: Request, session: Session = Depends(get_session)) -> Response:
        try:
            user = signed_in(request, session)
        except HTTPException as exc:
            return error_page(request, None, exc)
        pending = services.pending_approvals(session, user)
        return render(request, "approvals.html", user=user, pending=pending)

    @router.post("/approvals/{ticket_id}")
    def decide(
        request: Request,
        ticket_id: int,
        decision: str = Form(...),
        reason: str = Form(""),
        csrf: str = Form(""),
        session: Session = Depends(get_session),
    ) -> Response:
        user = None
        try:
            user = signed_in(request, session)
            check_csrf(request, csrf)
            if decision not in ("approve", "reject"):
                raise HTTPException(400, "Choose approve or reject.")
            if decision == "reject" and not reason.strip():
                flash(request, "Add a reason so the person knows why.")
                return go("/approvals")
            ticket = services.decide_approval(
                session, directory, base_url, user, ticket_id, decision, reason
            )
        except HTTPException as exc:
            return error_page(request, user, exc)
        flash(request, f"Ticket #{ticket.id}: {STATUS_LABELS[ticket.status.value]}.")
        return go("/approvals")

    # -- inbox, audit, password links -------------------------------------------------------

    @router.get("/inbox")
    def inbox_page(request: Request, session: Session = Depends(get_session)) -> Response:
        try:
            user = signed_in(request, session)
        except HTTPException as exc:
            return error_page(request, None, exc)
        return render(request, "inbox.html", user=user, messages=services.inbox(session, user))

    @router.get("/audit")
    def audit_page(
        request: Request,
        action: str | None = None,
        target: str | None = None,
        session: Session = Depends(get_session),
    ) -> Response:
        user = None
        try:
            user = signed_in(request, session)
            events = services.audit_log(session, user, target or None, action or None, None, 200)
        except HTTPException as exc:
            return error_page(request, user, exc)
        return render(request, "audit.html", user=user, events=events, action=action, target=target)

    @router.get("/reset/{token}")
    def reset_page(
        request: Request, token: str, session: Session = Depends(get_session)
    ) -> Response:
        try:
            record = services.find_link(session, token)
        except HTTPException as exc:
            return render(request, "error.html", exc.status_code, message=exc.detail)
        return render(
            request, "reset.html", username=record.username, min_length=MIN_PASSWORD_LENGTH
        )

    @router.post("/reset/{token}")
    def reset(
        request: Request,
        token: str,
        password: str = Form(""),
        confirm: str = Form(""),
        csrf: str = Form(""),
        session: Session = Depends(get_session),
    ) -> Response:
        check_csrf(request, csrf)
        try:
            record = services.find_link(session, token)
        except HTTPException as exc:
            return render(request, "error.html", exc.status_code, message=exc.detail)
        problem = None
        if len(password) < MIN_PASSWORD_LENGTH:
            problem = f"Use at least {MIN_PASSWORD_LENGTH} characters."
        elif password != confirm:
            problem = "The two passwords don't match."
        if problem is None:
            try:
                services.redeem_link(session, directory, token, password)
            except HTTPException as exc:
                problem = exc.detail
        if problem:
            return render(
                request,
                "reset.html",
                422,
                username=record.username,
                min_length=MIN_PASSWORD_LENGTH,
                error=problem,
            )
        flash(request, "Password set. You can sign in with it now.")
        return go("/login")

    app.include_router(router)


def services_closed() -> set[Status]:
    from app.models import CLOSED_STATUSES

    return CLOSED_STATUSES


def form_options(user: User) -> dict:
    types = ["password_reset", "access_request", "hardware"]
    if user.role != Role.EMPLOYEE:
        types += ["new_hire", "offboarding"]
    return {
        "types": types,
        "groups": REQUESTABLE_GROUPS,
        "departments": DEPARTMENTS,
        "today": utcnow().date().isoformat(),
    }


DETAIL_FIELDS = {
    TicketType.ACCESS_REQUEST.value: ["group"],
    TicketType.NEW_HIRE.value: ["first_name", "last_name", "department", "start_date"],
    TicketType.OFFBOARDING.value: ["username", "last_day"],
}


def ticket_from_form(form: dict) -> TicketCreate:
    ticket_type = form.get("type", "")
    details = {f: form.get(f, "").strip() for f in DETAIL_FIELDS.get(ticket_type, [])}
    return TicketCreate.model_validate(
        {
            "type": ticket_type,
            "title": form.get("title", ""),
            "description": form.get("description", ""),
            "details": details,
        }
    )


FIELD_NAMES = {
    "title": "Title",
    "description": "Description",
    "type": "Request type",
    "group": "Group",
    "first_name": "First name",
    "last_name": "Last name",
    "department": "Department",
    "start_date": "Start date",
    "username": "Username",
    "last_day": "Last day",
}


def friendly(error: dict) -> str:
    field = next((str(p) for p in reversed(error["loc"]) if str(p) in FIELD_NAMES), None)
    label = FIELD_NAMES.get(field, "This form")
    message = error["msg"].removeprefix("Value error, ")
    return f"{label}: {message}"
