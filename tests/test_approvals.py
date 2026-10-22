from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.auth import hash_token
from app.models import AuditEvent, Role, Ticket, User
from tests.conftest import auth


def request_access(client, who, group, **extra):
    body = {
        "type": "access_request",
        "title": f"Need {group}",
        "description": "Needed for month-end reporting",
        "details": {"group": group},
        **extra,
    }
    return client.post("/api/tickets", json=body, headers=auth(who))


def decide(client, who, ticket_id, decision, reason=None, headers=None):
    body = {"decision": decision}
    if reason is not None:
        body["reason"] = reason
    return client.post(f"/api/approvals/{ticket_id}", json=body, headers=headers or auth(who))


def audit_rows(app):
    with app.state.session_factory() as session:
        return [
            (e.actor, e.action, e.target, e.detail) for e in session.scalars(select(AuditEvent))
        ]


def inbox_subjects(client, who):
    return [m["subject"] for m in client.get("/api/inbox", headers=auth(who)).json()]


def add_user(app, username, role, manager=None):
    """Create an extra portal user and return auth headers for them."""
    with app.state.session_factory() as session:
        manager_id = None
        if manager:
            manager_id = session.scalar(select(User.id).where(User.username == manager))
        session.add(
            User(
                username=username,
                full_name=username.title(),
                department="IT",
                role=role,
                manager_id=manager_id,
                token_hash=hash_token(f"{username}-token"),
            )
        )
        session.commit()
    return {"Authorization": f"Bearer {username}-token"}


# -- starting a request ---------------------------------------------------------


def test_request_waits_for_manager_with_sla_paused(client, app):
    body = request_access(client, "emma", "tableau-users").json()
    assert body["status"] == "waiting_approval"
    assert body["sla_breached"] is False
    with app.state.session_factory() as session:
        assert session.get(Ticket, body["id"]).paused_at is not None
    assert inbox_subjects(client, "liam") == ["Approval needed: Emma Ruiz wants tableau-users"]


def test_manager_sees_only_their_teams_requests(client, app):
    ticket_id = request_access(client, "emma", "tableau-users").json()["id"]
    assert [t["id"] for t in client.get("/api/approvals", headers=auth("liam")).json()] == [
        ticket_id
    ]
    other_manager = add_user(app, "omar", Role.MANAGER)
    assert client.get("/api/approvals", headers=other_manager).json() == []
    assert client.get("/api/approvals", headers=auth("emma")).json() == []
    assert client.get("/api/approvals", headers=auth("ava")).json() == []  # not IT's turn yet


def test_unknown_group_is_rejected(client):
    assert request_access(client, "emma", "domain-admins").status_code == 422


def test_already_a_member_resolves_without_approval(client, directory):
    directory.add_to_group("emma", "vpn-users")
    body = request_access(client, "emma", "vpn-users").json()
    assert body["status"] == "auto_resolved"
    assert inbox_subjects(client, "emma") == ["You already have vpn-users"]


def test_request_without_directory_account_goes_to_it(client, directory):
    directory.delete_user("emma")
    body = request_access(client, "emma", "vpn-users").json()
    assert body["status"] == "in_progress"
    assert "No directory account" in body["automation_error"]


# -- manager decisions ------------------------------------------------------------


def test_manager_approval_grants_access(client, app, directory):
    ticket_id = request_access(client, "emma", "tableau-users").json()["id"]
    body = decide(client, "liam", ticket_id, "approve").json()

    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None
    assert body["details"]["manager_approved_by"] == "liam"
    assert "tableau-users" in directory.groups_of("emma")
    assert ("liam", "approval.manager", "emma", "tableau-users") in audit_rows(app)
    assert ("autopilot", "group.add", "emma", "tableau-users") in audit_rows(app)
    assert "Access granted: tableau-users" in inbox_subjects(client, "emma")


def test_rejection_needs_a_reason(client):
    ticket_id = request_access(client, "emma", "tableau-users").json()["id"]
    assert decide(client, "liam", ticket_id, "reject").status_code == 422
    assert decide(client, "liam", ticket_id, "reject", reason="   ").status_code == 422


def test_manager_rejection(client, app, directory):
    ticket_id = request_access(client, "emma", "tableau-users").json()["id"]
    body = decide(client, "liam", ticket_id, "reject", reason="Use the shared report").json()

    assert body["status"] == "rejected"
    assert body["details"]["rejection_reason"] == "Use the shared report"
    assert "tableau-users" not in directory.groups_of("emma")
    assert ("liam", "approval.reject", "emma", "tableau-users: Use the shared report") in (
        audit_rows(app)
    )
    assert "Access request rejected: tableau-users" in inbox_subjects(client, "emma")


def test_only_the_requesters_manager_can_decide(client, app):
    ticket_id = request_access(client, "emma", "tableau-users").json()["id"]
    other_manager = add_user(app, "omar", Role.MANAGER)
    assert decide(client, None, ticket_id, "approve", headers=other_manager).status_code == 404
    assert decide(client, "emma", ticket_id, "approve").status_code == 404
    assert decide(client, "ava", ticket_id, "approve").status_code == 404


def test_cannot_decide_twice_or_on_missing_ticket(client):
    ticket_id = request_access(client, "emma", "tableau-users").json()["id"]
    decide(client, "liam", ticket_id, "approve")
    assert decide(client, "liam", ticket_id, "reject", reason="changed mind").status_code == 404
    assert decide(client, "liam", 999, "approve").status_code == 404


def test_grant_failure_goes_to_it(client, directory):
    ticket_id = request_access(client, "emma", "tableau-users").json()["id"]
    directory.fail_on_group = "tableau-users"
    body = decide(client, "liam", ticket_id, "approve").json()
    assert body["status"] == "in_progress"
    assert "tableau-users" in body["automation_error"]


# -- privileged groups and IT approval ---------------------------------------------


def test_admin_access_needs_manager_and_it(client, app, directory):
    ticket_id = request_access(client, "emma", "it-admins").json()["id"]

    after_manager = decide(client, "liam", ticket_id, "approve").json()
    assert after_manager["status"] == "waiting_it_approval"
    assert "it-admins" not in directory.groups_of("emma")
    assert "Manager approved it-admins" in inbox_subjects(client, "emma")
    assert client.get("/api/approvals", headers=auth("liam")).json() == []
    assert [t["id"] for t in client.get("/api/approvals", headers=auth("ava")).json()] == [
        ticket_id
    ]

    done = decide(client, "ava", ticket_id, "approve").json()
    assert done["status"] == "resolved"
    assert done["details"]["it_approved_by"] == "ava"
    assert "it-admins" in directory.groups_of("emma")
    actions = [(r[0], r[1]) for r in audit_rows(app)]
    assert actions == [
        ("liam", "approval.manager"),
        ("ava", "approval.it"),
        ("autopilot", "group.add"),
    ]


def test_it_can_reject_after_manager_approves(client, directory):
    ticket_id = request_access(client, "emma", "it-admins").json()["id"]
    decide(client, "liam", ticket_id, "approve")
    body = decide(client, "ava", ticket_id, "reject", reason="Not needed for this role").json()
    assert body["status"] == "rejected"
    assert body["details"]["rejected_by"] == "ava"
    assert "it-admins" not in directory.groups_of("emma")


def test_nobody_approves_their_own_request(client, app):
    # Ava has no manager, so her request goes straight to IT.
    ticket_id = request_access(client, "ava", "it-admins").json()["id"]
    assert client.get(f"/api/tickets/{ticket_id}", headers=auth("ava")).json()["status"] == (
        "waiting_it_approval"
    )
    response = decide(client, "ava", ticket_id, "approve")
    assert response.status_code == 403
    assert response.json()["detail"] == "You can't approve your own request"
    assert ("ava", "approval.denied", "ava", "tried to approve own request for it-admins") in (
        audit_rows(app)
    )

    second_agent = add_user(app, "zoe", Role.AGENT)
    assert decide(client, None, ticket_id, "approve", headers=second_agent).json()["status"] == (
        "resolved"
    )


# -- SLA pause ---------------------------------------------------------------------


def test_time_waiting_for_manager_does_not_count(client, app):
    ticket_id = request_access(client, "emma", "tableau-users").json()["id"]
    with app.state.session_factory() as session:
        ticket = session.get(Ticket, ticket_id)
        # Pretend the manager took 3 days to answer.
        ticket.paused_at = ticket.paused_at - timedelta(days=3)
        ticket.created_at = ticket.created_at - timedelta(days=3)
        ticket.due_at = ticket.due_at - timedelta(days=3)
        original_due = ticket.due_at
        session.commit()

    assert (
        client.get(f"/api/tickets/{ticket_id}", headers=auth("emma")).json()["sla_breached"]
        is False
    )
    body = decide(client, "liam", ticket_id, "approve").json()
    assert body["sla_breached"] is False
    new_due = datetime.fromisoformat(body["due_at"])
    pushed_back = new_due - original_due.replace(tzinfo=UTC)
    assert timedelta(days=3) - timedelta(minutes=1) < pushed_back < timedelta(days=3, minutes=1)


def test_agent_status_change_also_restarts_clock(client, app):
    ticket_id = request_access(client, "emma", "tableau-users").json()["id"]
    client.patch(
        f"/api/tickets/{ticket_id}/status", json={"status": "in_progress"}, headers=auth("ava")
    )
    with app.state.session_factory() as session:
        assert session.get(Ticket, ticket_id).paused_at is None


# -- audit log ----------------------------------------------------------------------


def test_audit_log_is_for_agents_only(client):
    assert client.get("/api/audit", headers=auth("liam")).status_code == 403
    assert client.get("/api/audit", headers=auth("emma")).status_code == 403
    assert client.get("/api/audit").status_code == 401


def test_audit_log_filters_and_order(client):
    first = request_access(client, "emma", "tableau-users").json()["id"]
    decide(client, "liam", first, "approve")
    second = request_access(client, "emma", "vpn-users").json()["id"]
    decide(client, "liam", second, "reject", reason="Office only")

    everything = client.get("/api/audit", headers=auth("ava")).json()
    assert [e["action"] for e in everything] == [
        "approval.reject",
        "group.add",
        "approval.manager",
    ]
    by_ticket = client.get(f"/api/audit?ticket_id={first}", headers=auth("ava")).json()
    assert {e["ticket_id"] for e in by_ticket} == {first}
    by_action = client.get("/api/audit?action=group.add", headers=auth("ava")).json()
    assert len(by_action) == 1
    by_target = client.get("/api/audit?target=nobody", headers=auth("ava")).json()
    assert by_target == []
    partial = client.get("/api/audit?action=APPROVAL&target=Emm", headers=auth("ava")).json()
    assert {e["action"] for e in partial} == {"approval.manager", "approval.reject"}
    wildcard = client.get("/api/audit?action=%25", headers=auth("ava")).json()
    assert wildcard == []  # "%" is matched literally, not as a SQL wildcard
    limited = client.get("/api/audit?limit=1", headers=auth("ava")).json()
    assert len(limited) == 1
    assert client.get("/api/audit?limit=0", headers=auth("ava")).status_code == 422


def test_audit_log_cannot_be_changed_through_the_api(client):
    assert client.put("/api/audit", headers=auth("ava")).status_code == 405
    assert client.delete("/api/audit", headers=auth("ava")).status_code == 405


@pytest.mark.parametrize("change", ["update", "delete"])
def test_database_refuses_to_change_audit_rows(app, change):
    with app.state.session_factory() as session:
        session.add(AuditEvent(actor="x", action="test", target="y", detail=""))
        session.commit()
        event = session.scalar(select(AuditEvent))
        if change == "update":
            event.detail = "tampered"
        else:
            session.delete(event)
        with pytest.raises(DBAPIError, match="append-only"):
            session.commit()


def test_audit_log_is_newest_first_by_time(client, app):
    from datetime import timedelta

    from app.models import utcnow

    now = utcnow()
    with app.state.session_factory() as session:
        # Added out of time order, like imported demo data.
        for hours_ago, action in [(1, "middle"), (5, "oldest"), (0, "newest")]:
            session.add(
                AuditEvent(
                    at=now - timedelta(hours=hours_ago), actor="x", action=action, target="y"
                )
            )
        session.commit()
    events = client.get("/api/audit", headers=auth("ava")).json()
    assert [e["action"] for e in events] == ["newest", "middle", "oldest"]
