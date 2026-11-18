"""Web portal tests (server-rendered pages, no browser needed)."""

import html
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AuditEvent, Status, Ticket, utcnow
from app.portal import friendly, linkify, safe_next, span, time_left

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


def test_it_can_download_the_audit_log_from_the_portal(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="password_reset", title="Locked out")
    assert portal.get("/audit.csv").status_code == 403
    submit(portal, "/logout", "/tickets")
    assert portal.get("/audit.csv").url.path == "/login"
    sign_in(portal, "ava")
    assert 'href="/audit.csv?action=&amp;target="' in portal.get("/audit").text
    assert 'href="/audit.csv?action=group.add&amp;target=em%20ma"' in (
        portal.get("/audit?action=group.add&target=em ma").text
    )
    download = portal.get("/audit.csv?target=emma")
    assert download.headers["content-type"] == "text/csv; charset=utf-8"
    assert "password_link.issue,emma" in download.text
    assert portal.get("/audit.csv?target=nobody").text.count("\n") == 1


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


# -- time left on the SLA -------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (20, "under 1m"),
        (12 * 60, "12m"),
        (2 * 3600 + 300, "2h 5m"),
        (3 * 86400 + 4 * 3600, "3d 4h"),
    ],
)
def test_span_uses_the_two_biggest_units(seconds, text):
    assert span(seconds) == text
    assert span(-seconds) == text


def test_time_left_in_words():
    now = utcnow()
    soon = Ticket(due_at=now + timedelta(hours=2, minutes=5, seconds=30), status=Status.NEW)
    assert time_left(soon) == "2h 5m left"
    late = Ticket(due_at=now - timedelta(minutes=40, seconds=30), status=Status.IN_PROGRESS)
    assert time_left(late) == "late by 40m"
    paused = Ticket(
        due_at=now + timedelta(hours=5),
        paused_at=now - timedelta(hours=1),
        status=Status.WAITING_APPROVAL,
    )
    assert time_left(paused) == "paused, 6h 0m left"
    assert time_left(Ticket(due_at=now, status=Status.RESOLVED)) == ""


def test_ticket_pages_show_the_time_left(portal):
    sign_in(portal, "emma")
    page = new_ticket(portal, type="hardware", title="Broken dock")
    assert re.search(r'<span class="left">[\w ]+ left</span>', page.text)
    assert "left</span>" in portal.get("/tickets").text
    done = new_ticket(portal, type="password_reset", title="Reset please")
    assert "left</span>" not in done.text  # closed tickets have no countdown


# -- search and the past due tab -----------------------------------------------------


def test_search_box_finds_tickets_and_keeps_the_words(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Broken dock")
    new_ticket(portal, type="hardware", title="Sticky keyboard")
    page = portal.get("/tickets?q=DOCK").text
    assert "Broken dock" in page and "Sticky keyboard" not in page
    assert 'value="DOCK"' in page
    assert 'href="/tickets?show=all&amp;q=DOCK"' in page  # the tabs keep the search
    nothing = portal.get("/tickets?q=<script>alert(1)</script>").text
    assert "<script>alert(1)" not in nothing
    assert "No tickets match" in nothing


def test_past_due_tab_lists_only_late_open_tickets(portal, app):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Broken dock")
    new_ticket(portal, type="hardware", title="Sticky keyboard")
    assert "Nothing is past due." in portal.get("/tickets?show=overdue").text
    with app.state.session_factory() as session:
        late = session.scalar(select(Ticket).where(Ticket.title == "Broken dock"))
        late.due_at = utcnow() - timedelta(hours=3)
        session.commit()
    page = portal.get("/tickets?show=overdue").text
    assert "Broken dock" in page and "Sticky keyboard" not in page
    assert "late by 3h 0m" in page
    # An unknown tab falls back to the open list instead of failing.
    assert portal.get("/tickets?show=nonsense").status_code == 200


def test_the_ticket_page_takes_a_reply(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Monitor flickers")
    path = "/tickets/1"
    submit(portal, f"{path}/comments", path, body="It started this morning.")
    page = portal.get(path).text
    assert "It started this morning." in page
    assert "Emma Ruiz" in page


def test_an_employee_is_not_offered_the_internal_note_box(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Monitor flickers")
    assert 'name="internal"' not in portal.get("/tickets/1").text


def test_an_agent_gets_the_internal_note_box_and_the_requester_does_not_see_the_note(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Monitor flickers")
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})

    sign_in(portal, "ava")
    assert 'name="internal"' in portal.get("/tickets/1").text
    submit(portal, "/tickets/1/comments", "/tickets/1", body="Dock is faulty.", internal="on")
    assert "Internal note" in portal.get("/tickets/1").text
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})

    sign_in(portal, "emma")
    page = portal.get("/tickets/1").text
    assert "Dock is faulty." not in page
    assert "Internal note" not in page


def test_an_empty_conversation_says_so(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Monitor flickers")
    assert "Nothing here yet" in portal.get("/tickets/1").text


def test_an_agent_can_assign_a_ticket_from_the_page(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Monitor flickers")
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})

    sign_in(portal, "ava")
    assert "Nobody yet" in portal.get("/tickets/1").text
    submit(portal, "/tickets/1/assignee", "/tickets/1", assignee="ava")
    assert "Ava Patel" in portal.get("/tickets/1").text


def test_the_queue_has_mine_and_unassigned_tabs_for_agents_only(portal):
    sign_in(portal, "emma")
    assert "show=mine" not in portal.get("/tickets").text
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})

    sign_in(portal, "ava")
    page = portal.get("/tickets").text
    assert "show=mine" in page and "show=unassigned" in page


def test_the_mine_tab_shows_only_tickets_you_own(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Mine to fix")
    new_ticket(portal, type="hardware", title="Someone elses")
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})

    sign_in(portal, "ava")
    submit(portal, "/tickets/1/assignee", "/tickets/1", assignee="ava")
    mine = portal.get("/tickets?show=mine").text
    assert "Mine to fix" in mine and "Someone elses" not in mine
    waiting = portal.get("/tickets?show=unassigned").text
    assert "Someone elses" in waiting and "Mine to fix" not in waiting


def test_the_ticket_page_shows_when_the_first_reply_came(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Docking station is dead")
    assert "Waiting for a first reply" in portal.get("/tickets/1").text
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})

    sign_in(portal, "ava")
    submit(portal, "/tickets/1/comments", "/tickets/1", body="Looking at it now.")
    page = portal.get("/tickets/1").text
    assert "Waiting for a first reply" not in page
    assert "after it arrived" in page


def test_a_resolved_ticket_offers_a_reopen_box(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Printer jams")
    assert "Still not fixed?" not in portal.get("/tickets/1").text
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})

    sign_in(portal, "ava")
    submit(portal, "/tickets/1/status", "/tickets/1", status="resolved")
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})

    sign_in(portal, "emma")
    assert "Still not fixed?" in portal.get("/tickets/1").text
    submit(portal, "/tickets/1/reopen", "/tickets/1", reason="Jammed again.")
    page = portal.get("/tickets/1").text
    assert "Reopened: Jammed again." in page
    assert "1 time" in page


def test_an_agent_can_change_priority_from_the_page(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Second monitor not detected")
    assert 'name="priority"' not in portal.get("/tickets/1").text
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})

    sign_in(portal, "ava")
    submit(portal, "/tickets/1/priority", "/tickets/1", priority="P1")
    assert ">P1<" in portal.get("/tickets/1").text


def test_an_unknown_priority_from_the_page_is_refused(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Second monitor not detected")
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})
    sign_in(portal, "ava")
    assert submit(portal, "/tickets/1/priority", "/tickets/1", priority="P9").status_code == 400


def resolve_ticket_one(portal):
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})
    sign_in(portal, "ava")
    submit(portal, "/tickets/1/status", "/tickets/1", status="resolved")
    portal.post("/logout", data={"csrf": csrf(portal, "/tickets")})


def test_a_resolved_ticket_asks_the_requester_how_it_went(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Keyboard keys stick")
    assert "How did we do?" not in portal.get("/tickets/1").text
    resolve_ticket_one(portal)

    sign_in(portal, "emma")
    assert "How did we do?" in portal.get("/tickets/1").text
    submit(portal, "/tickets/1/rating", "/tickets/1", score="5", comment="Quick, thanks.")
    page = portal.get("/tickets/1").text
    assert "5 out of 5" in page and "Quick, thanks." in page
    assert "How did we do?" not in page  # asked once


def test_an_agent_is_not_asked_to_rate_someone_elses_ticket(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Keyboard keys stick")
    resolve_ticket_one(portal)
    sign_in(portal, "ava")
    assert "How did we do?" not in portal.get("/tickets/1").text


def test_a_score_outside_the_range_is_refused_by_the_page(portal):
    sign_in(portal, "emma")
    new_ticket(portal, type="hardware", title="Keyboard keys stick")
    resolve_ticket_one(portal)
    sign_in(portal, "emma")
    assert submit(portal, "/tickets/1/rating", "/tickets/1", score="9").status_code == 400


def test_the_help_page_lists_and_searches_articles(portal):
    sign_in(portal, "emma")
    page = portal.get("/kb").text
    assert "I forgot my password" in page
    found = portal.get("/kb?q=printer").text
    assert "The printer will not print" in found
    assert "I forgot my password" not in found


def test_a_search_with_no_matches_offers_a_ticket(portal):
    sign_in(portal, "emma")
    page = portal.get("/kb?q=zzzznothing").text
    assert "Nothing matched" in page and "/tickets/new" in page


def test_reading_an_article_shows_its_text(portal):
    sign_in(portal, "emma")
    page = portal.get("/kb/laptop-will-not-charge").text
    assert "Try a different outlet" in page
    assert "Raise a ticket" in page


def test_an_unknown_article_is_a_404_page(portal):
    sign_in(portal, "emma")
    assert portal.get("/kb/no-such-page").status_code == 404


def test_the_new_request_form_suggests_help_for_the_chosen_type(portal):
    sign_in(portal, "emma")
    assert "may answer it" not in portal.get("/tickets/new").text
    page = portal.get("/tickets/new?type=hardware").text
    assert "may answer it" in page
    assert "My laptop will not charge" in page
    assert "I forgot my password" not in page


def test_help_is_in_the_navigation(portal):
    sign_in(portal, "emma")
    assert 'href="/kb"' in portal.get("/tickets").text
