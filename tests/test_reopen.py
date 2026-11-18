"""Reopening a ticket when the fix did not hold."""

from datetime import timedelta

import pytest

from app.models import Status, Ticket, utcnow
from tests.conftest import TOKENS


def auth(name):
    return {"Authorization": f"Bearer {TOKENS[name]}"}


def a_resolved_ticket(client, who="emma"):
    body = {"type": "hardware", "title": "Printer jams", "description": "Every third page."}
    ticket_id = client.post("/api/tickets", json=body, headers=auth(who)).json()["id"]
    client.patch(
        f"/api/tickets/{ticket_id}/status", json={"status": "resolved"}, headers=auth("ava")
    )
    return ticket_id


def reopen(client, ticket_id, reason="It jammed again this morning.", who="emma"):
    return client.post(
        f"/api/tickets/{ticket_id}/reopen", json={"reason": reason}, headers=auth(who)
    )


def test_the_requester_can_reopen_their_own_ticket(client):
    ticket_id = a_resolved_ticket(client)
    reopened = reopen(client, ticket_id).json()
    assert reopened["status"] == "in_progress"
    assert reopened["resolved_at"] is None
    assert reopened["reopen_count"] == 1


def test_reopening_gives_the_ticket_a_fresh_clock(client, app):
    ticket_id = a_resolved_ticket(client)
    with app.state.session_factory() as session:
        ticket = session.get(Ticket, ticket_id)
        ticket.due_at = utcnow() - timedelta(days=3)  # the first attempt ran long
        session.commit()
    reopened = reopen(client, ticket_id).json()
    assert reopened["sla_breached"] is False
    assert reopened["sla_seconds_left"] > 0


def test_the_reason_becomes_the_first_reply_on_the_ticket(client):
    ticket_id = a_resolved_ticket(client)
    reopen(client, ticket_id, "Still jamming on page three.")
    comments = client.get(f"/api/tickets/{ticket_id}/comments", headers=auth("emma")).json()
    assert comments[-1]["body"] == "Reopened: Still jamming on page three."


def test_reopening_is_counted_each_time(client):
    ticket_id = a_resolved_ticket(client)
    for _ in range(2):
        reopen(client, ticket_id)
        client.patch(
            f"/api/tickets/{ticket_id}/status", json={"status": "resolved"}, headers=auth("ava")
        )
    assert client.get(f"/api/tickets/{ticket_id}", headers=auth("emma")).json()["reopen_count"] == 2


@pytest.mark.parametrize("reason", ["", "   "])
def test_reopening_needs_a_reason(client, reason):
    assert reopen(client, a_resolved_ticket(client), reason).status_code == 422


def test_an_open_ticket_cannot_be_reopened(client):
    body = {"type": "hardware", "title": "Printer jams", "description": "Every third page."}
    ticket_id = client.post("/api/tickets", json=body, headers=auth("emma")).json()["id"]
    refused = reopen(client, ticket_id)
    assert refused.status_code == 422
    assert "Only a resolved ticket" in refused.json()["detail"]


def test_a_rejected_request_cannot_be_reopened(client, app):
    """Rejection was a decision, not a fix that failed. Ask again with a new ticket."""
    ticket_id = a_resolved_ticket(client)
    with app.state.session_factory() as session:
        ticket = session.get(Ticket, ticket_id)
        ticket.status = Status.REJECTED
        session.commit()
    assert reopen(client, ticket_id).status_code == 422


def test_a_ticket_closed_long_ago_cannot_be_reopened(client, app):
    ticket_id = a_resolved_ticket(client)
    with app.state.session_factory() as session:
        ticket = session.get(Ticket, ticket_id)
        ticket.resolved_at = utcnow() - timedelta(days=8)
        session.commit()
    refused = reopen(client, ticket_id)
    assert refused.status_code == 422
    assert "more than 7 days ago" in refused.json()["detail"]


def test_you_cannot_reopen_someone_elses_ticket(client):
    ticket_id = a_resolved_ticket(client, who="liam")
    assert reopen(client, ticket_id, who="emma").status_code == 404


def test_the_owner_hears_that_it_came_back(client):
    ticket_id = a_resolved_ticket(client)
    client.patch(
        f"/api/tickets/{ticket_id}/assignee", json={"assignee": "ava"}, headers=auth("ava")
    )
    reopen(client, ticket_id)
    inbox = client.get("/api/inbox", headers=auth("ava")).json()
    assert any(f"#{ticket_id} is back" in m["subject"] for m in inbox)


def test_reopens_show_up_in_the_metrics(client):
    reopen(client, a_resolved_ticket(client))
    assert 'helpdesk_tickets_reopened_total{type="hardware"} 1.0' in client.get("/metrics").text


def test_every_reopen_is_in_the_audit_log(client):
    ticket_id = a_resolved_ticket(client)
    reopen(client, ticket_id, "Jammed again.")
    log = client.get(f"/api/audit?ticket_id={ticket_id}", headers=auth("ava")).json()
    entry = next(e for e in log if e["action"] == "ticket.reopen")
    assert entry["detail"] == "reopen #1: Jammed again."
    assert entry["actor"] == "emma"
