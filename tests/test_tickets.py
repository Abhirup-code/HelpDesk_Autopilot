from datetime import datetime

from tests.conftest import auth

NEW_TICKET = {
    "type": "password_reset",
    "title": "Locked out of email",
    "description": "MFA prompt keeps failing after phone change.",
}


def create(client, who="emma", **overrides):
    return client.post("/api/tickets", json={**NEW_TICKET, **overrides}, headers=auth(who))


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_create_ticket_uses_default_priority_and_due_time(client):
    response = create(client)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "new"
    assert body["priority"] == "P2"
    created = datetime.fromisoformat(body["created_at"])
    due = datetime.fromisoformat(body["due_at"])
    assert (due - created).total_seconds() == 4 * 3600
    assert body["sla_breached"] is False


def test_create_ticket_with_priority_override(client):
    body = create(client, type="hardware", priority="P1").json()
    assert body["priority"] == "P1"


def test_blank_description_is_rejected(client):
    assert create(client, description="   ").status_code == 422


def test_unknown_ticket_type_is_rejected(client):
    assert create(client, type="coffee").status_code == 422


def test_missing_token_is_401(client):
    assert client.post("/api/tickets", json=NEW_TICKET).status_code == 401


def test_invalid_token_is_401(client):
    headers = {"Authorization": "Bearer nope"}
    assert client.get("/api/tickets", headers=headers).status_code == 401


def test_employee_sees_only_own_tickets(client):
    create(client, who="emma")
    create(client, who="liam", title="Need Tableau access", type="access_request")
    mine = client.get("/api/tickets", headers=auth("emma")).json()
    assert [t["title"] for t in mine] == ["Locked out of email"]


def test_other_users_ticket_is_404(client):
    ticket_id = create(client, who="liam").json()["id"]
    assert client.get(f"/api/tickets/{ticket_id}", headers=auth("emma")).status_code == 404


def test_owner_can_read_own_ticket(client):
    ticket_id = create(client).json()["id"]
    response = client.get(f"/api/tickets/{ticket_id}", headers=auth("emma"))
    assert response.status_code == 200
    assert response.json()["id"] == ticket_id


def test_missing_ticket_is_404(client):
    assert client.get("/api/tickets/999", headers=auth("ava")).status_code == 404


def test_agent_sees_all_tickets_and_can_filter(client):
    create(client, who="emma")
    create(client, who="liam", title="Laptop screen cracked", type="hardware")
    everything = client.get("/api/tickets", headers=auth("ava")).json()
    assert len(everything) == 2
    hardware = client.get("/api/tickets?type=hardware", headers=auth("ava")).json()
    assert [t["title"] for t in hardware] == ["Laptop screen cracked"]
    p3 = client.get("/api/tickets?priority=P3", headers=auth("ava")).json()
    assert len(p3) == 1
    new = client.get("/api/tickets?status=new", headers=auth("ava")).json()
    assert len(new) == 2


def test_queue_is_sorted_by_due_time(client):
    create(client, type="hardware", title="Low priority mouse", priority="P4")
    create(client, type="offboarding", title="Offboard contractor")
    titles = [t["title"] for t in client.get("/api/tickets", headers=auth("ava")).json()]
    assert titles == ["Offboard contractor", "Low priority mouse"]


def test_agent_resolves_ticket(client):
    ticket_id = create(client).json()["id"]
    response = client.patch(
        f"/api/tickets/{ticket_id}/status", json={"status": "resolved"}, headers=auth("ava")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None
    assert body["sla_breached"] is False


def test_reopening_clears_resolved_time(client):
    ticket_id = create(client).json()["id"]
    url = f"/api/tickets/{ticket_id}/status"
    client.patch(url, json={"status": "resolved"}, headers=auth("ava"))
    body = client.patch(url, json={"status": "in_progress"}, headers=auth("ava")).json()
    assert body["resolved_at"] is None


def test_employee_cannot_change_status(client):
    ticket_id = create(client).json()["id"]
    response = client.patch(
        f"/api/tickets/{ticket_id}/status", json={"status": "resolved"}, headers=auth("emma")
    )
    assert response.status_code == 403
