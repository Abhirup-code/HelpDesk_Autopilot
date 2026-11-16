"""Giving a ticket to an IT agent, and the queue views built on that."""

from tests.conftest import TOKENS


def auth(name):
    return {"Authorization": f"Bearer {TOKENS[name]}"}


def a_ticket(client, who="emma", title="Laptop will not charge"):
    body = {"type": "hardware", "title": title, "description": "It stops at 2%."}
    return client.post("/api/tickets", json=body, headers=auth(who)).json()["id"]


def assign(client, ticket_id, to, who="ava"):
    return client.patch(
        f"/api/tickets/{ticket_id}/assignee", json={"assignee": to}, headers=auth(who)
    )


def test_a_new_ticket_belongs_to_nobody(client):
    ticket_id = a_ticket(client)
    assert client.get(f"/api/tickets/{ticket_id}", headers=auth("ava")).json()["assignee"] is None


def test_an_agent_takes_a_ticket(client):
    ticket_id = a_ticket(client)
    assert assign(client, ticket_id, "ava").json()["assignee"] == "ava"


def test_a_ticket_goes_back_to_the_queue(client):
    ticket_id = a_ticket(client)
    assign(client, ticket_id, "ava")
    assert assign(client, ticket_id, None).json()["assignee"] is None


def test_only_an_agent_can_own_a_ticket(client):
    ticket_id = a_ticket(client)
    refused = assign(client, ticket_id, "emma")
    assert refused.status_code == 422
    assert "not an IT agent" in refused.json()["detail"]


def test_an_unknown_username_is_refused(client):
    assert assign(client, a_ticket(client), "nobody-here").status_code == 422


def test_an_employee_cannot_assign_tickets(client):
    ticket_id = a_ticket(client)
    assert assign(client, ticket_id, "ava", who="emma").status_code == 403


def test_every_change_of_owner_is_in_the_audit_log(client):
    ticket_id = a_ticket(client)
    assign(client, ticket_id, "ava")
    assign(client, ticket_id, None)
    log = client.get(f"/api/audit?ticket_id={ticket_id}", headers=auth("ava")).json()
    moves = [e["detail"] for e in log if e["action"] == "ticket.assign"]
    assert moves == ["ava -> unassigned", "unassigned -> ava"]


def test_assigning_it_to_the_same_person_changes_nothing(client):
    ticket_id = a_ticket(client)
    assign(client, ticket_id, "ava")
    assign(client, ticket_id, "ava")
    log = client.get(f"/api/audit?ticket_id={ticket_id}", headers=auth("ava")).json()
    assert len([e for e in log if e["action"] == "ticket.assign"]) == 1


def test_the_new_owner_hears_about_it(client, app):
    """Liam is a manager here, so promote a second agent to receive the handover."""
    from sqlalchemy import select

    from app.models import Role, User

    with app.state.session_factory() as session:
        liam = session.scalar(select(User).where(User.username == "liam"))
        liam.role = Role.AGENT
        session.commit()

    ticket_id = a_ticket(client)
    assign(client, ticket_id, "liam", who="ava")
    inbox = client.get("/api/inbox", headers=auth("liam")).json()
    assert any(f"#{ticket_id} is yours" in m["subject"] for m in inbox)


def test_picking_up_a_ticket_yourself_sends_no_email(client):
    ticket_id = a_ticket(client)
    before = len(client.get("/api/inbox", headers=auth("ava")).json())
    assign(client, ticket_id, "ava", who="ava")
    assert len(client.get("/api/inbox", headers=auth("ava")).json()) == before


def test_filtering_the_queue_by_owner(client):
    mine = a_ticket(client, title="Mine")
    theirs = a_ticket(client, title="Theirs")
    assign(client, mine, "ava")

    owned = client.get("/api/tickets?assignee=ava", headers=auth("ava")).json()
    assert [t["id"] for t in owned] == [mine]

    waiting = client.get("/api/tickets?assignee=unassigned", headers=auth("ava")).json()
    assert theirs in [t["id"] for t in waiting]
    assert mine not in [t["id"] for t in waiting]


def test_the_total_count_matches_the_owner_filter(client):
    assign(client, a_ticket(client), "ava")
    a_ticket(client)
    page = client.get("/api/tickets?assignee=ava", headers=auth("ava"))
    assert page.headers["X-Total-Count"] == "1"
