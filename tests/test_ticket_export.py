"""Downloading the queue as a spreadsheet."""

import csv
import io
import re

from tests.conftest import TOKENS

PASSWORDS = {"emma": "Str0ng-Passphrase-1", "ava": "Str0ng-Passphrase-3"}


def auth(name):
    return {"Authorization": f"Bearer {TOKENS[name]}"}


def rows(body: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body)))


def csrf(client, path="/login"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def sign_in(client, user, directory):
    directory.set_password(user, PASSWORDS[user])
    return client.post(
        "/login",
        data={
            "username": user,
            "password": PASSWORDS[user],
            "csrf": csrf(client),
            "next": "/tickets",
        },
    )


def a_ticket(client, who="emma", title="Laptop will not charge", **extra):
    body = {"type": "hardware", "title": title, "description": "It stops at 2%.", **extra}
    return client.post("/api/tickets", json=body, headers=auth(who)).json()["id"]


def test_the_download_has_a_row_per_ticket_with_the_numbers_that_matter(client, directory):
    ticket_id = a_ticket(client)
    client.patch(
        f"/api/tickets/{ticket_id}/assignee", json={"assignee": "ava"}, headers=auth("ava")
    )
    client.post(f"/api/tickets/{ticket_id}/comments", json={"body": "On it."}, headers=auth("ava"))
    sign_in(client, "ava", directory)

    response = client.get("/tickets.csv")
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="tickets.csv"' in response.headers["content-disposition"]

    row = rows(response.text)[0]
    assert row["id"] == str(ticket_id)
    assert row["title"] == "Laptop will not charge"
    assert row["requester"] == "emma" and row["assignee"] == "ava"
    assert row["priority"] == "P3" and row["status"] == "new"
    assert row["sla_breached"] == "no"
    assert int(row["first_response_seconds"]) >= 0
    assert row["reopen_count"] == "0"
    assert row["satisfaction"] == ""


def test_the_download_follows_the_tab_you_are_on(client, directory):
    mine = a_ticket(client, title="Mine")
    a_ticket(client, title="Theirs")
    client.patch(f"/api/tickets/{mine}/assignee", json={"assignee": "ava"}, headers=auth("ava"))
    sign_in(client, "ava", directory)

    owned = rows(client.get("/tickets.csv?show=mine").text)
    assert [r["title"] for r in owned] == ["Mine"]
    waiting = rows(client.get("/tickets.csv?show=unassigned").text)
    assert [r["title"] for r in waiting] == ["Theirs"]


def test_the_download_follows_the_search_box(client, directory):
    a_ticket(client, title="Printer jams")
    a_ticket(client, title="Laptop will not charge")
    sign_in(client, "ava", directory)
    found = rows(client.get("/tickets.csv?show=all&q=printer").text)
    assert [r["title"] for r in found] == ["Printer jams"]


def test_an_employee_only_downloads_their_own_tickets(client, directory):
    a_ticket(client, who="emma", title="Mine")
    a_ticket(client, who="liam", title="Not mine")
    sign_in(client, "emma", directory)
    titles = [r["title"] for r in rows(client.get("/tickets.csv?show=all").text)]
    assert titles == ["Mine"]


def test_a_resolved_ticket_carries_its_outcome(client, directory):
    ticket_id = a_ticket(client)
    client.patch(
        f"/api/tickets/{ticket_id}/status", json={"status": "resolved"}, headers=auth("ava")
    )
    client.post(f"/api/tickets/{ticket_id}/rating", json={"score": 5}, headers=auth("emma"))
    sign_in(client, "ava", directory)
    row = rows(client.get("/tickets.csv?show=all").text)[0]
    assert row["status"] == "resolved"
    assert row["resolved_utc"].endswith("Z")
    assert int(row["resolution_seconds"]) >= 0
    assert row["satisfaction"] == "5"


def test_a_title_that_looks_like_a_formula_is_defused(client, directory):
    """A ticket called "=cmd|..." must not run when the file opens in Excel."""
    a_ticket(client, title='=HYPERLINK("http://evil","click")')
    sign_in(client, "ava", directory)
    row = rows(client.get("/tickets.csv?show=all").text)[0]
    assert row["title"].startswith("'=")


def test_the_download_gives_a_stranger_nothing(client):
    """The portal sends you to sign in rather than answering with a 401, as its
    other pages do. What matters is that no ticket data comes back."""
    a_ticket(client, title="Private business")
    response = client.get("/tickets.csv")
    assert "Private business" not in response.text
    assert "text/csv" not in response.headers["content-type"]
