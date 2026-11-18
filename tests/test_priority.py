"""Changing a ticket's priority, and what that does to the due time."""

from datetime import datetime, timedelta

from app import sla
from app.models import Priority, Ticket, TicketType, aware
from tests.conftest import TOKENS


def auth(name):
    return {"Authorization": f"Bearer {TOKENS[name]}"}


def a_ticket(client, who="emma", **extra):
    body = {
        "type": "hardware",
        "title": "Second monitor not detected",
        "description": "Nothing on the second screen.",
        **extra,
    }
    return client.post("/api/tickets", json=body, headers=auth(who)).json()


def set_priority(client, ticket_id, priority, who="ava"):
    return client.patch(
        f"/api/tickets/{ticket_id}/priority", json={"priority": priority}, headers=auth(who)
    )


def due(response) -> datetime:
    return aware(datetime.fromisoformat(response["due_at"]))


def test_raising_the_priority_pulls_the_due_time_in(client):
    ticket = a_ticket(client)  # hardware defaults to P3: one business day
    before = due(ticket)
    after = due(set_priority(client, ticket["id"], "P1").json())
    assert after < before


def test_lowering_the_priority_pushes_the_due_time_out(client):
    ticket = a_ticket(client, priority="P1")
    before = due(ticket)
    after = due(set_priority(client, ticket["id"], "P4").json())
    assert after > before


def test_the_new_priority_is_what_the_sla_says_for_it(client):
    """P1 is one clock hour from when the ticket arrived."""
    ticket = a_ticket(client)
    changed = set_priority(client, ticket["id"], "P1").json()
    created = aware(datetime.fromisoformat(changed["created_at"]))
    assert due(changed) - created == timedelta(hours=1)


def test_setting_the_same_priority_changes_nothing(client):
    ticket = a_ticket(client)
    unchanged = set_priority(client, ticket["id"], ticket["priority"]).json()
    assert due(unchanged) == due(ticket)
    log = client.get(f"/api/audit?ticket_id={ticket['id']}", headers=auth("ava")).json()
    assert not [e for e in log if e["action"] == "ticket.priority"]


def test_time_banked_while_the_ticket_waited_survives_the_change(client, app):
    """A ticket that sat waiting for a manager had its due time pushed out by three
    hours. Changing priority must move that time, not recalculate it away."""
    ticket = a_ticket(client)
    with app.state.session_factory() as session:
        row = session.get(Ticket, ticket["id"])
        row.due_at = aware(row.due_at) + timedelta(hours=3)  # as set_status does on resume
        session.commit()
    before = due(client.get(f"/api/tickets/{ticket['id']}", headers=auth("ava")).json())
    after = due(set_priority(client, ticket["id"], "P1").json())

    created = aware(datetime.fromisoformat(ticket["created_at"]))
    was = sla.ticket_due_at(TicketType.HARDWARE, Priority.P3, created, {})
    now_due = sla.ticket_due_at(TicketType.HARDWARE, Priority.P1, created, {})
    assert after - before == now_due - was  # moved by the difference between the targets
    assert after == now_due + timedelta(hours=3)  # and the banked three hours are still there


def test_only_an_agent_can_change_priority(client):
    ticket = a_ticket(client)
    assert set_priority(client, ticket["id"], "P1", who="emma").status_code == 403


def test_a_closed_ticket_keeps_its_priority(client):
    ticket = a_ticket(client)
    client.patch(
        f"/api/tickets/{ticket['id']}/status", json={"status": "resolved"}, headers=auth("ava")
    )
    refused = set_priority(client, ticket["id"], "P1")
    assert refused.status_code == 422
    assert "its priority is final" in refused.json()["detail"]


def test_an_unknown_priority_is_refused(client):
    assert set_priority(client, a_ticket(client)["id"], "P9").status_code == 422


def test_the_change_is_in_the_audit_log(client):
    ticket = a_ticket(client)
    set_priority(client, ticket["id"], "P1")
    log = client.get(f"/api/audit?ticket_id={ticket['id']}", headers=auth("ava")).json()
    entry = next(e for e in log if e["action"] == "ticket.priority")
    assert entry["detail"] == "P3 -> P1"
