"""What the requester thought of the fix."""

import pytest

from tests.conftest import TOKENS


def auth(name):
    return {"Authorization": f"Bearer {TOKENS[name]}"}


def a_resolved_ticket(client, who="emma"):
    body = {"type": "hardware", "title": "Keyboard keys stick", "description": "Mostly the E."}
    ticket_id = client.post("/api/tickets", json=body, headers=auth(who)).json()["id"]
    client.patch(
        f"/api/tickets/{ticket_id}/status", json={"status": "resolved"}, headers=auth("ava")
    )
    return ticket_id


def rate(client, ticket_id, score, comment=None, who="emma"):
    body = {"score": score}
    if comment is not None:
        body["comment"] = comment
    return client.post(f"/api/tickets/{ticket_id}/rating", json=body, headers=auth(who))


def test_the_requester_rates_a_resolved_ticket(client):
    ticket_id = a_resolved_ticket(client)
    rated = rate(client, ticket_id, 5, "Fixed the same day, thank you.").json()
    assert rated["satisfaction"] == 5
    assert rated["satisfaction_comment"] == "Fixed the same day, thank you."


def test_a_score_on_its_own_is_enough(client):
    assert rate(client, a_resolved_ticket(client), 3).json()["satisfaction_comment"] is None


def test_a_blank_comment_is_stored_as_nothing(client):
    assert rate(client, a_resolved_ticket(client), 4, "   ").json()["satisfaction_comment"] is None


@pytest.mark.parametrize("score", [0, 6, -1, 99])
def test_a_score_outside_one_to_five_is_refused(client, score):
    assert rate(client, a_resolved_ticket(client), score).status_code == 422


def test_an_open_ticket_cannot_be_rated_yet(client):
    body = {"type": "hardware", "title": "Keyboard keys stick", "description": "Mostly the E."}
    ticket_id = client.post("/api/tickets", json=body, headers=auth("emma")).json()["id"]
    refused = rate(client, ticket_id, 5)
    assert refused.status_code == 422
    assert "once it is resolved" in refused.json()["detail"]


def test_rating_happens_once(client):
    ticket_id = a_resolved_ticket(client)
    rate(client, ticket_id, 5)
    again = rate(client, ticket_id, 1)
    assert again.status_code == 409
    assert client.get(f"/api/tickets/{ticket_id}", headers=auth("emma")).json()["satisfaction"] == 5


def test_an_agent_cannot_rate_their_own_work(client):
    ticket_id = a_resolved_ticket(client)
    refused = rate(client, ticket_id, 5, who="ava")
    assert refused.status_code == 403
    assert "person who raised the ticket" in refused.json()["detail"]


def test_you_cannot_rate_someone_elses_ticket(client):
    assert rate(client, a_resolved_ticket(client, who="liam"), 1).status_code == 404


def test_the_score_reaches_the_metrics(client):
    rate(client, a_resolved_ticket(client), 4)
    rate(client, a_resolved_ticket(client), 2)
    body = client.get("/metrics").text
    assert "helpdesk_satisfaction_mean 3.0" in body
    assert "helpdesk_tickets_rated_ratio 1.0" in body


def test_unrated_tickets_pull_the_response_rate_down(client):
    rate(client, a_resolved_ticket(client), 5)
    a_resolved_ticket(client)
    assert "helpdesk_tickets_rated_ratio 0.5" in client.get("/metrics").text


def test_no_ratings_yet_is_not_a_score_of_zero_tickets(client):
    body = client.get("/metrics").text
    assert "helpdesk_satisfaction_mean 0.0" in body
    assert "helpdesk_tickets_rated_ratio 0.0" in body


def test_the_rating_is_in_the_audit_log(client):
    ticket_id = a_resolved_ticket(client)
    rate(client, ticket_id, 4)
    log = client.get(f"/api/audit?ticket_id={ticket_id}", headers=auth("ava")).json()
    assert next(e for e in log if e["action"] == "ticket.rated")["detail"] == "4 out of 5"
