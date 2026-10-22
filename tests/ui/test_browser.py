"""End-to-end journeys in a real browser (Chromium via Playwright)."""

import re

import pytest
from playwright.sync_api import expect

from tests.ui.conftest import sign_in, sign_out

pytestmark = pytest.mark.ui


def test_employee_resets_their_password(page):
    sign_in(page, "emma")
    expect(page.get_by_role("heading", name="My tickets")).to_be_visible()

    page.get_by_role("link", name="New request").first.click()
    page.get_by_label("What do you need?").select_option("password_reset")
    page.get_by_label("Title").fill("Locked out of email")
    page.get_by_label("Description").fill("MFA prompt keeps failing")
    page.get_by_role("button", name="Submit request").click()
    expect(page.locator(".pill")).to_have_text("Done automatically")

    page.get_by_role("link", name="Inbox").click()
    expect(page.get_by_role("heading", name="Your password reset link")).to_be_visible()
    page.locator(".message a").first.click()

    expect(page.get_by_role("heading", name="Set a new password")).to_be_visible()
    page.get_by_label("New password").fill("brand-new-pass-42")
    page.get_by_label("Type it again").fill("brand-new-pass-42")
    page.get_by_role("button", name="Set password").click()
    expect(page.get_by_text("Password set. You can sign in with it now.")).to_be_visible()

    sign_in(page, "emma", "brand-new-pass-42")
    expect(page.get_by_role("heading", name="My tickets")).to_be_visible()


def test_wrong_password_shows_a_generic_message(page):
    sign_in(page, "emma", "definitely-wrong-pw")
    expect(page.get_by_role("alert")).to_have_text("Wrong username or password.")


def test_form_shows_only_the_fields_for_the_chosen_type(page):
    sign_in(page, "liam")
    page.goto("/tickets/new")
    access = page.locator('fieldset[data-for="access_request"]')
    new_hire = page.locator('fieldset[data-for="new_hire"]')
    page.get_by_label("What do you need?").select_option("access_request")
    expect(access).to_be_visible()
    expect(new_hire).to_be_hidden()
    page.get_by_label("What do you need?").select_option("new_hire")
    expect(new_hire).to_be_visible()
    expect(access).to_be_hidden()


def test_manager_approves_an_access_request(page, server):
    _, directory = server
    sign_in(page, "emma")
    page.goto("/tickets/new")
    page.get_by_label("What do you need?").select_option("access_request")
    page.get_by_label("Title").fill("Need Tableau")
    page.get_by_label("Description").fill("Month-end reports")
    page.get_by_label("Group").select_option("tableau-users")
    page.get_by_role("button", name="Submit request").click()
    expect(page.locator(".pill")).to_have_text("Waiting for manager")
    ticket_url = page.url
    sign_out(page)

    sign_in(page, "liam")
    page.get_by_role("link", name="Approvals").click()
    expect(page.get_by_text("Emma Ruiz wants")).to_be_visible()
    page.get_by_role("button", name="Approve").click()
    expect(page.locator(".flash")).to_contain_text("Resolved")
    expect(page.get_by_text("No requests are waiting for you.")).to_be_visible()
    assert "tableau-users" in directory.groups_of("emma")
    sign_out(page)

    sign_in(page, "emma")
    page.goto(ticket_url)
    expect(page.locator(".pill")).to_have_text("Resolved")
    expect(page.locator(".timeline")).to_contain_text("approval.manager")


def test_it_agent_sees_queue_audit_log_and_dashboard_link(page):
    sign_in(page, "emma")
    page.goto("/tickets/new")
    page.get_by_label("What do you need?").select_option("hardware")
    page.get_by_label("Title").fill("Monitor flickers")
    page.get_by_label("Description").fill("Second screen goes black")
    page.get_by_role("button", name="Submit request").click()
    sign_out(page)

    sign_in(page, "ava")
    expect(page.get_by_role("heading", name="IT queue")).to_be_visible()
    page.get_by_role("link", name="Monitor flickers").click()
    page.get_by_label("Status").select_option("resolved")
    page.get_by_role("button", name="Update").click()
    expect(page.locator(".flash")).to_have_text("Status changed to Resolved.")

    page.get_by_role("link", name="Audit log").click()
    expect(page.get_by_role("heading", name="Audit log")).to_be_visible()
    expect(page.get_by_role("link", name=re.compile("Dashboard"))).to_be_visible()


def test_employee_cannot_open_the_audit_log(page):
    sign_in(page, "emma")
    expect(page.get_by_role("link", name="Audit log")).to_have_count(0)
    page.goto("/audit")
    expect(page.get_by_role("heading", name="Not allowed")).to_be_visible()
