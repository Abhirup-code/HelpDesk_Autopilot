"""Time to first reply: the other number a help desk is judged on."""

from tests.conftest import TOKENS


def auth(name):
    return {"Authorization": f"Bearer {TOKENS[name]}"}


def a_ticket(client, who="emma"):
    body = {"type": "hardware", "title": "Docking station is dead", "description": "No power."}
    return client.post("/api/tickets", json=body, headers=auth(who)).json()["id"]


def reply(client, ticket_id, body, who="ava", internal=False):
    return client.post(
        f"/api/tickets/{ticket_id}/comments",
        json={"body": body, "internal": internal},
        headers=auth(who),
    )


def get(client, ticket_id, who="ava"):
    return client.get(f"/api/tickets/{ticket_id}", headers=auth(who)).json()


def test_a_new_ticket_has_had_no_reply(client):
    ticket = get(client, a_ticket(client))
    assert ticket["first_response_at"] is None
    assert ticket["first_response_seconds"] is None


def test_the_first_agent_reply_starts_the_clock(client):
    ticket_id = a_ticket(client)
    reply(client, ticket_id, "Looking at it now.")
    ticket = get(client, ticket_id)
    assert ticket["first_response_at"] is not None
    assert ticket["first_response_seconds"] >= 0


def test_only_the_first_reply_counts(client):
    ticket_id = a_ticket(client)
    reply(client, ticket_id, "Looking at it now.")
    first = get(client, ticket_id)["first_response_at"]
    reply(client, ticket_id, "Replacement is on the way.")
    assert get(client, ticket_id)["first_response_at"] == first


def test_the_requester_talking_to_themselves_is_not_a_reply(client):
    ticket_id = a_ticket(client)
    reply(client, ticket_id, "Any update?", who="emma")
    assert get(client, ticket_id)["first_response_at"] is None


def test_an_internal_note_is_not_a_reply(client):
    """The note helps IT, but the person waiting has still heard nothing."""
    ticket_id = a_ticket(client)
    reply(client, ticket_id, "Known fault on this model.", internal=True)
    assert get(client, ticket_id)["first_response_at"] is None
    reply(client, ticket_id, "We are sending a replacement.")
    assert get(client, ticket_id)["first_response_at"] is not None


def test_the_metrics_report_the_median_and_how_many_were_answered(client):
    answered = a_ticket(client)
    a_ticket(client)  # nobody replies to this one
    reply(client, answered, "On it.")
    body = client.get("/metrics").text
    assert 'helpdesk_first_response_seconds_median{type="hardware"}' in body
    assert "helpdesk_tickets_answered_ratio 0.5" in body


def test_the_answered_share_is_zero_with_no_tickets(client):
    assert "helpdesk_tickets_answered_ratio 0.0" in client.get("/metrics").text
