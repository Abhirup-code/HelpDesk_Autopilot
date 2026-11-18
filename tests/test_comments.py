"""Replies on a ticket, and internal notes only IT can see."""

from tests.conftest import TOKENS


def auth(name):
    return {"Authorization": f"Bearer {TOKENS[name]}"}


def a_ticket(client, who="emma"):
    body = {
        "type": "hardware",
        "title": "Laptop will not charge",
        "description": "It stops at 2%.",
    }
    return client.post("/api/tickets", json=body, headers=auth(who)).json()["id"]


def test_a_reply_is_stored_and_read_back(client):
    ticket_id = a_ticket(client)
    created = client.post(
        f"/api/tickets/{ticket_id}/comments",
        json={"body": "  Have you tried another charger?  "},
        headers=auth("ava"),
    )
    assert created.status_code == 201
    comment = created.json()
    assert comment["author"] == "ava" and comment["internal"] is False
    assert comment["body"] == "Have you tried another charger?"  # trimmed

    listed = client.get(f"/api/tickets/{ticket_id}/comments", headers=auth("emma")).json()
    assert [c["body"] for c in listed] == ["Have you tried another charger?"]


def test_comments_come_back_oldest_first(client):
    ticket_id = a_ticket(client)
    for text in ["first", "second", "third"]:
        client.post(f"/api/tickets/{ticket_id}/comments", json={"body": text}, headers=auth("emma"))
    listed = client.get(f"/api/tickets/{ticket_id}/comments", headers=auth("emma")).json()
    assert [c["body"] for c in listed] == ["first", "second", "third"]


def test_the_requester_never_sees_an_internal_note(client):
    ticket_id = a_ticket(client)
    client.post(
        f"/api/tickets/{ticket_id}/comments",
        json={"body": "Known fault on this model, swap it.", "internal": True},
        headers=auth("ava"),
    )
    client.post(
        f"/api/tickets/{ticket_id}/comments",
        json={"body": "We are sending a replacement."},
        headers=auth("ava"),
    )
    mine = client.get(f"/api/tickets/{ticket_id}/comments", headers=auth("emma")).json()
    assert [c["body"] for c in mine] == ["We are sending a replacement."]
    theirs = client.get(f"/api/tickets/{ticket_id}/comments", headers=auth("ava")).json()
    assert len(theirs) == 2


def test_only_an_agent_can_write_an_internal_note(client):
    ticket_id = a_ticket(client)
    refused = client.post(
        f"/api/tickets/{ticket_id}/comments",
        json={"body": "secret", "internal": True},
        headers=auth("emma"),
    )
    assert refused.status_code == 403
    assert client.get(f"/api/tickets/{ticket_id}/comments", headers=auth("ava")).json() == []


def test_a_blank_comment_is_refused(client):
    ticket_id = a_ticket(client)
    for body in ["", "   ", "\n\t"]:
        refused = client.post(
            f"/api/tickets/{ticket_id}/comments", json={"body": body}, headers=auth("emma")
        )
        assert refused.status_code == 422


def test_you_cannot_read_or_write_comments_on_someone_elses_ticket(client):
    ticket_id = a_ticket(client, "liam")
    assert client.get(f"/api/tickets/{ticket_id}/comments", headers=auth("emma")).status_code == 404
    posted = client.post(
        f"/api/tickets/{ticket_id}/comments", json={"body": "hello"}, headers=auth("emma")
    )
    assert posted.status_code == 404


def test_a_reply_from_someone_else_reaches_the_requester_inbox(client):
    ticket_id = a_ticket(client)
    client.post(
        f"/api/tickets/{ticket_id}/comments",
        json={"body": "We are sending a replacement."},
        headers=auth("ava"),
    )
    inbox = client.get("/api/inbox", headers=auth("emma")).json()
    assert any(f"#{ticket_id}" in m["subject"] for m in inbox)
    assert any("We are sending a replacement." in m["body"] for m in inbox)


def test_your_own_comment_does_not_email_you(client):
    ticket_id = a_ticket(client)
    before = len(client.get("/api/inbox", headers=auth("emma")).json())
    client.post(
        f"/api/tickets/{ticket_id}/comments", json={"body": "Any update?"}, headers=auth("emma")
    )
    assert len(client.get("/api/inbox", headers=auth("emma")).json()) == before


def test_an_internal_note_sends_no_email(client):
    ticket_id = a_ticket(client)
    before = len(client.get("/api/inbox", headers=auth("emma")).json())
    client.post(
        f"/api/tickets/{ticket_id}/comments",
        json={"body": "Swap it, known fault.", "internal": True},
        headers=auth("ava"),
    )
    assert len(client.get("/api/inbox", headers=auth("emma")).json()) == before
