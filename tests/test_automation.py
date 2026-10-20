from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import automation
from app.automation import make_username
from app.models import AuditEvent, ResetToken
from tests.conftest import auth

TODAY = datetime.now(UTC).date()


def post(client, who, **ticket):
    ticket.setdefault("title", "Automation test")
    ticket.setdefault("description", "Created by a test")
    return client.post("/api/tickets", json=ticket, headers=auth(who))


def audit(app):
    with app.state.session_factory() as session:
        return [(e.action, e.target, e.detail) for e in session.scalars(select(AuditEvent))]


def inbox(client, who):
    return client.get("/api/inbox", headers=auth(who)).json()


def link_token(message_body: str) -> str:
    return message_body.rsplit("/api/password-resets/", 1)[1].strip()


# -- usernames ----------------------------------------------------------------


def test_username_is_first_initial_and_last_name():
    assert make_username("Jane", "Doe", lambda u: False) == "jdoe"


def test_username_strips_accents_and_symbols():
    assert make_username("Zoë", "O'Brien-Núñez", lambda u: False) == "zobriennunez"


def test_username_gets_number_when_taken():
    taken = {"jdoe", "jdoe2"}
    assert make_username("Jane", "Doe", taken.__contains__) == "jdoe3"


# -- password reset -----------------------------------------------------------


def test_password_reset_auto_resolves_and_sends_link(client, app):
    body = post(client, "emma", type="password_reset").json()
    assert body["status"] == "auto_resolved"
    assert body["resolved_at"] is not None

    messages = inbox(client, "emma")
    assert len(messages) == 1
    assert "15 minutes" in messages[0]["body"]
    assert "http://test/api/password-resets/" in messages[0]["body"]
    assert inbox(client, "liam") == []  # nobody else sees it

    assert ("password_link.issue", "emma", "valid for 15 minutes") in audit(app)


def test_reset_token_is_stored_only_as_hash(client, app):
    post(client, "emma", type="password_reset")
    token = link_token(inbox(client, "emma")[0]["body"])
    with app.state.session_factory() as session:
        stored = session.scalar(select(ResetToken))
    assert token not in stored.token_hash
    assert len(stored.token_hash) == 64


def test_redeem_link_sets_password_once(client, app, directory):
    post(client, "emma", type="password_reset")
    token = link_token(inbox(client, "emma")[0]["body"])
    url = f"/api/password-resets/{token}"

    first = client.post(url, json={"new_password": "correct-horse-battery-1"})
    assert first.status_code == 204
    assert directory.people["emma"].password_set
    assert ("password.set", "emma", "via one-time link") in audit(app)

    again = client.post(url, json={"new_password": "correct-horse-battery-2"})
    assert again.status_code == 400


def test_expired_link_is_rejected(client, app):
    post(client, "emma", type="password_reset")
    token = link_token(inbox(client, "emma")[0]["body"])
    with app.state.session_factory() as session:
        record = session.scalar(select(ResetToken))
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    response = client.post(
        f"/api/password-resets/{token}", json={"new_password": "correct-horse-battery-1"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "This link is invalid, expired or already used"


def test_made_up_link_is_rejected(client):
    response = client.post(
        "/api/password-resets/not-a-real-token", json={"new_password": "correct-horse-battery-1"}
    )
    assert response.status_code == 400


def test_weak_passwords_are_rejected(client):
    post(client, "emma", type="password_reset")
    url = f"/api/password-resets/{link_token(inbox(client, 'emma')[0]['body'])}"
    assert client.post(url, json={"new_password": "short"}).status_code == 422
    contains_name = client.post(url, json={"new_password": "Emma-likes-coffee-99"})
    assert contains_name.status_code == 422
    assert "username" in contains_name.json()["detail"]


def test_directory_refusing_password_gives_generic_error(client, directory):
    post(client, "emma", type="password_reset")
    url = f"/api/password-resets/{link_token(inbox(client, 'emma')[0]['body'])}"
    directory.disable_user("emma")
    response = client.post(url, json={"new_password": "correct-horse-battery-1"})
    assert response.status_code == 400


def test_password_reset_without_directory_account_goes_to_it(client, app, directory):
    directory.delete_user("emma")
    body = post(client, "emma", type="password_reset").json()
    assert body["status"] == "in_progress"
    assert "No directory account" in body["automation_error"]
    assert any(a[0] == "automation.failed" for a in audit(app))


# -- new hire -----------------------------------------------------------------

NEW_HIRE = {
    "type": "new_hire",
    "details": {
        "first_name": "Jane",
        "last_name": "Doe",
        "department": "Engineering",
        "start_date": (TODAY + timedelta(days=7)).isoformat(),
    },
}


def test_new_hire_creates_account_with_department_groups(client, app, directory):
    body = post(client, "liam", **NEW_HIRE).json()
    assert body["status"] == "auto_resolved"
    assert body["details"]["username"] == "jdoe"
    assert directory.groups_of("jdoe") == ["all-staff", "git-users", "vpn-users"]

    actions = [a[:2] for a in audit(app)]
    assert actions[:4] == [
        ("user.create", "jdoe"),
        ("group.add", "jdoe"),
        ("group.add", "jdoe"),
        ("group.add", "jdoe"),
    ]
    message = inbox(client, "liam")[0]
    assert message["subject"] == "New hire ready: Jane Doe"
    assert "Username: jdoe" in message["body"]
    assert "72 hours" in message["body"]
    assert ("password_link.issue", "jdoe", "valid for 72 hours") in audit(app)


def test_second_jane_doe_gets_unique_username(client, directory):
    post(client, "liam", **NEW_HIRE)
    body = post(client, "liam", **NEW_HIRE).json()
    assert body["details"]["username"] == "jdoe2"


def test_new_hire_rolls_back_if_a_group_fails(client, app, directory):
    directory.fail_on_group = "vpn-users"
    body = post(client, "liam", **NEW_HIRE).json()
    assert body["status"] == "in_progress"
    assert "vpn-users" in body["automation_error"]
    assert not directory.user_exists("jdoe")
    assert directory.groups_of("jdoe") == []
    actions = [a[0] for a in audit(app)]
    assert "user.rollback" in actions
    assert actions[-1] == "automation.failed"


def test_new_hire_failure_before_create_has_no_rollback(client, app, directory):
    directory.create_user("placeholder", "P", "H", "IT")

    def broken_create(*args):
        from app.directory import DirectoryError

        raise DirectoryError("directory is read only")

    directory.create_user = broken_create
    body = post(client, "liam", **NEW_HIRE).json()
    assert body["automation_error"] == "directory is read only"
    assert "user.rollback" not in [a[0] for a in audit(app)]


def test_employee_cannot_request_new_hire(client):
    response = post(client, "emma", **NEW_HIRE)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "bad",
    [
        {"department": "Marketing"},
        {"first_name": "J4ne"},
        {"start_date": "next week"},
        {"salary": 100},
    ],
)
def test_new_hire_details_are_validated(client, bad):
    ticket = {**NEW_HIRE, "details": {**NEW_HIRE["details"], **bad}}
    assert post(client, "liam", **ticket).status_code == 422


def test_details_not_allowed_on_other_ticket_types(client):
    assert post(client, "emma", type="hardware", details={"x": 1}).status_code == 422


def test_new_hire_due_by_start_date_when_sooner(client):
    start = TODAY + timedelta(days=1)
    ticket = {**NEW_HIRE, "details": {**NEW_HIRE["details"], "start_date": start.isoformat()}}
    body = post(client, "liam", **ticket).json()
    due = datetime.fromisoformat(body["due_at"])
    created = datetime.fromisoformat(body["created_at"])
    assert due <= datetime(start.year, start.month, start.day, 9, tzinfo=UTC)
    assert due >= created


# -- offboarding ----------------------------------------------------------------


def offboarding(username="emma", last_day=TODAY - timedelta(days=1)):
    return {
        "type": "offboarding",
        "details": {"username": username, "last_day": last_day.isoformat()},
    }


def test_offboarding_after_last_day_runs_immediately(client, app, directory):
    directory.add_to_group("emma", "finance-share")
    body = post(client, "liam", **offboarding()).json()
    assert body["status"] == "auto_resolved"
    assert directory.is_disabled("emma")
    assert directory.groups_of("emma") == []

    actions = [a[:3] for a in audit(app)]
    assert ("group.remove", "emma", "all-staff") in actions
    assert ("group.remove", "emma", "finance-share") in actions
    assert ("user.disable", "emma", "groups removed: all-staff, finance-share") in actions
    assert inbox(client, "liam")[0]["subject"] == "Offboarding complete: emma"


def test_future_offboarding_waits_for_last_day(client, directory, monkeypatch):
    last_day = TODAY + timedelta(days=3)
    body = post(client, "liam", **offboarding(last_day=last_day)).json()
    assert body["status"] == "new"
    assert not directory.is_disabled("emma")
    due = datetime.fromisoformat(body["due_at"])
    assert due == datetime(last_day.year, last_day.month, last_day.day, tzinfo=UTC) + timedelta(
        days=1, hours=1
    )

    run = client.post("/api/automation/run-due", headers=auth("ava")).json()
    assert run == {"offboardings_processed": 0}

    day_after = last_day + timedelta(days=1)
    monkeypatch.setattr("app.routes.accounts.today_utc", lambda: day_after)
    run = client.post("/api/automation/run-due", headers=auth("ava")).json()
    assert run == {"offboardings_processed": 1}
    assert directory.is_disabled("emma")


def test_offboarding_unknown_user_is_rejected(client):
    response = post(client, "liam", **offboarding(username="ghost"))
    assert response.status_code == 422
    assert "ghost" in response.json()["detail"]


def test_offboarding_twice_goes_to_it(client, directory):
    post(client, "liam", **offboarding())
    body = post(client, "liam", **offboarding()).json()
    assert body["status"] == "in_progress"
    assert "already disabled" in body["automation_error"]


def test_employee_cannot_offboard_people(client):
    assert post(client, "emma", **offboarding(username="liam")).status_code == 403


def test_only_agents_can_run_automations(client):
    assert client.post("/api/automation/run-due", headers=auth("liam")).status_code == 403


def test_inbox_needs_sign_in(client):
    assert client.get("/api/inbox").status_code == 401


def test_today_utc_is_a_date():
    assert isinstance(automation.today_utc(), date)
