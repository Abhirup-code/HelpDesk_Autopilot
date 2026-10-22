"""Web portal tests (server-rendered pages, no browser needed)."""

import html
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AuditEvent
from app.portal import friendly, linkify, safe_next

PASSWORDS = {"emma": "emma-password-1", "liam": "liam-password-1", "ava": "ava-password-1"}


@pytest.fixture
def portal(app, directory):
    for user, password in PASSWORDS.items():
        directory.set_password(user, password)
    return TestClient(app)


def csrf(client, path="/login"):
    page = client.get(path).text
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def sign_in(client, user, password=None):
    return client.post(
        "/login",
        data={
            "username": user,
            "password": password or PASSWORDS[user],
            "csrf": csrf(client),
            "next": "/tickets",
        },
    )


def submit(client, path, form_page, **data):
    return client.post(path, data={"csrf": csrf(client, form_page), **data})


def new_ticket(client, **data):
    data.setdefault("title", "Portal test")
    data.setdefault("description", "Created in the portal")
    return submit(client, "/tickets/new", "/tickets/new", **data)


# -- sign in -----------------------------------------------------------------------


def test_signed_out_people_go_to_sign_in(portal):
    assert portal.get("/").url.path == "/login"
    page = portal.get("/tickets")
    assert page.url.path == "/login"
    assert page.url.query == b"next=/tickets" or "next=/tickets" in str(page.url)


def test_wrong_password_is_rejected_without_saying_why(portal):
    page = sign_in(portal, "emma", "not-her-password")
    assert page.status_code == 401
    assert "Wrong username or password." in page.text
    unknown = sign_in(portal, "nobody", "whatever-password")
    assert "Wrong username or password." in unknown.text


def test_sign_in_and_out(portal):
    page = sign_in(portal, "emma")
    assert page.url.path == "/tickets"
    assert "My tickets" in page.text
    assert "Emma Ruiz" in page.text
    out = submit(portal, "/logout", "/tickets")
    assert out.url.path == "/login"
    assert "You signed out." in out.text
    assert portal.get("/tickets").url.path == "/login"


def test_forms_need_a_csrf_token(portal):
    page = portal.post("/login", data={"username": "emma", "password": PASSWORDS["emma"]})
    assert page.status_code == 400


def test_repeated_wrong_passwords_lock_the_account(portal, app):
    for _ in range(5):
        sign_in(portal, "emma", "guess-password-x")
    locked = sign_in(portal, "emma")  # even the right password is refused while locked
    assert locked.status_code == 429
    assert "Too many wrong passwords" in locked.text
    with app.state.session_factory() as session:
        actions = [e.action for e in session.scalars(select(AuditEvent))]
    assert actions == ["signin.locked"]
    app.state.login_throttle.reset("emma")
    assert sign_in(portal, "emma").url.path == "/tickets"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/inbox", "/inbox"),
        ("/tickets/new", "/tickets/new"),
        ("/tickets/42", "/tickets/42"),
        ("/tickets/42/../../evil", "/tickets"),
        ("/tickets?x=https://evil.example", "/tickets"),
        ("//evil.example", "/tickets"),
        ("https://evil.example/x", "/tickets"),
        (None, "/tickets"),
        ("", "/tickets"),
    ],
)
def test_redirect_after_sign_in_stays_on_this_site(target, expected):
    assert safe_next(target) == expected


def test_pages_send_a_strict_content_security_policy(portal):
    csp = portal.get("/login").headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


# -- tickets ------------------------------------------------------------------------


def test_password_reset_from_start_to_finish(portal, directory):
    sign_in(portal, "emma")
    created = new_ticket(portal, type="password_reset", title="Locked out")
    assert re.search(r"/tickets/\d+$", created.url.path)
    assert "Done automatically" in created.text

    inbox = portal.get("/inbox").text
    link = re.search(r'href="(http://test/reset/[^"]+)"', inbox).group(1)
    reset_path = link.removeprefix("http://test")
    submit(portal, "/logout", "/tickets")

    form = portal.get(reset_path)
    assert "Set a new password" in form.text and "emma" in form.text
    short = submit(portal, reset_path, reset_path, password="short", confirm="short")
    assert short.status_code == 422 and "at least 12" in short.text
    mismatch = submit(
        portal, reset_path, reset_path, password="new-password-123", confirm="new-password-124"
    )
    assert "don't match" in html.unescape(mismatch.text)
    has_name = submit(
        portal, reset_path, reset_path, password="emma-new-pass-1", confirm="emma-new-pass-1"
    )
    assert "must not contain your username" in has_name.text

    done = submit(
        portal, reset_path, reset_path, password="brand-new-pass-77", confirm="brand-new-pass-77"
    )
    assert done.url.path == "/login"
    assert "Password set." in done.text
    assert sign_in(portal, "emma", "brand-new-pass-77").url.path == "/tickets"

    reused = portal.get(reset_path)
    assert reused.status_code == 400
    assert "invalid, expired or already used" in reused.text
    again = portal.post(reset_path, data={"csrf": csrf(portal), "password": "x", "confirm": "x"})
    assert again.status_code == 400


def test_new_request_form_matches_role(portal):
    sign_in(portal, "emma")
    form = portal.get("/tickets/new").text
    assert 'value="new_hire"' not in form
    refused = new_ticket(
        portal,
        type="new_hire",
        first_name="Jane",
        last_name="Doe",
        department="Finance",
        start_date="2030-01-07",
    )
    assert refused.status_code == 403
    assert "Only managers and IT" in refused.text


def test_form_errors_are_explained(portal):
    sign_in(portal, "emma")
    page = new_ticket(portal, type="hardware", title="x")
    assert page.status_code == 422
    assert "Title:" in page.text
    assert 'value="x"' in page.text  # keeps what the person typed


def test_manager_creates_new_hire_and_offboarding(portal, directory):
    sign_in(portal, "liam")
    hire = new_ticket(
        portal,
        type="new_hire",
        first_name="Jane",
        last_name="Doe",
        department="Engineering",
        start_date="2030-01-07",
    )
    assert "Done automatically" in hire.text
    assert directory.user_exists("jdoe")
    unknown = new_ticket(portal, type="offboarding", username="ghost", last_day="2026-01-01")
    assert unknown.status_code == 422
    assert "No directory account named ghost" in unknown.text


def test_ticket_list_tabs_and_privacy(portal):
    sign_in(portal, "liam")
    mine = new_ticket(portal, type="hardware", title="Liam laptop")
    ticket_path = mine.url.path
    submit(portal, "/logout", "/tickets")
    sign_in(portal, "emma")
    assert portal.get(ticket_path).status_code == 404
    assert "Nothing here" in portal.get("/tickets?show=all").text


def test_agent_queue_and_status_change(portal):
    sign_in(portal, "emma")
    path = new_ticket(portal, type="hardware", title="Monitor flickers").url.path
    ticket_id = path.rsplit("/", 1)[1]
    refused = submit(portal, f"{path}/status", path, status="resolved")
    assert refused.status_code == 403
    submit(portal, "/logout", "/tickets")

    sign_in(portal, "ava")
    queue = portal.get("/tickets").text
    assert "IT queue" in queue and "Monitor flickers" in queue
    changed = submit(portal, f"{path}/status", path, status="resolved")
    assert "Status changed to Resolved" in changed.text
    bad = submit(portal, f"/tickets/{ticket_id}/status", path, status="exploded")
    assert bad.status_code == 400


# -- approvals and audit ---------------------------------------------------------------


def test_manager_approves_and_rejects(portal, directory):
    sign_in(portal, "emma")
    first = new_ticket(portal, type="access_request", group="tableau-users").url.path
    second = new_ticket(portal, type="access_request", group="vpn-users").url.path
    submit(portal, "/logout", "/tickets")

    sign_in(portal, "liam")
    page = portal.get("/approvals").text
    assert "Emma Ruiz wants" in page and "tableau-users" in page
    first_id, second_id = first.rsplit("/", 1)[1], second.rsplit("/", 1)[1]
    approved = submit(portal, f"/approvals/{first_id}", "/approvals", decision="approve")
    assert f"Ticket #{first_id}: Resolved." in approved.text
    assert "tableau-users" in directory.groups_of("emma")

    no_reason = submit(portal, f"/approvals/{second_id}", "/approvals", decision="reject")
    assert "Add a reason" in no_reason.text
    rejected = submit(
        portal, f"/approvals/{second_id}", "/approvals", decision="reject", reason="Office only"
    )
    assert f"Ticket #{second_id}: Rejected." in rejected.text
    odd = submit(portal, f"/approvals/{second_id}", "/approvals", decision="maybe")
    assert odd.status_code == 400
    assert "No requests are waiting" in portal.get("/approvals").text


def test_self_approval_is_blocked_in_the_portal(portal):
    sign_in(portal, "ava")
    path = new_ticket(portal, type="access_request", group="vpn-users").url.path
    ticket_id = path.rsplit("/", 1)[1]
    page = submit(portal, f"/approvals/{ticket_id}", "/approvals", decision="approve")
    assert page.status_code == 403
    assert "You can't approve your own request" in html.unescape(page.text)
    history = portal.get(path).text
    assert "approval.denied" in history


def test_audit_page_is_for_it_only(portal):
    sign_in(portal, "emma")
    assert portal.get("/audit").status_code == 403
    new_ticket(portal, type="password_reset", title="Locked out")
    submit(portal, "/logout", "/tickets")
    sign_in(portal, "ava")
    page = portal.get("/audit").text
    assert "password_link.issue" in page
    filtered = portal.get("/audit?action=nothing.like.this").text
    assert "No entries match." in filtered
    assert "Dashboard" in page  # agents get the Grafana link


def test_bad_reset_link_page(portal):
    page = portal.get("/reset/not-a-real-token")
    assert page.status_code == 400


# -- helpers ----------------------------------------------------------------------------


def test_linkify_escapes_html_and_links_urls():
    html = str(linkify('Click http://x.test/reset/abc <script>alert("hi")</script>'))
    assert '<a href="http://x.test/reset/abc">' in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_friendly_error_names_the_field():
    error = {"loc": ("body", "details", "first_name"), "msg": "Value error, bad"}
    assert friendly(error) == "First name: bad"
    assert friendly({"loc": ("x",), "msg": "oops"}) == "This form: oops"
