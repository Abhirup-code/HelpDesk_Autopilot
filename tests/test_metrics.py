from datetime import timedelta

from prometheus_client.parser import text_string_to_metric_families

from app.models import Ticket, utcnow
from tests.conftest import auth


def scrape(client) -> dict[tuple[str, tuple], float]:
    response = client.get("/metrics")
    assert response.status_code == 200
    samples = {}
    for family in text_string_to_metric_families(response.text):
        for s in family.samples:
            samples[(s.name, tuple(sorted(s.labels.items())))] = s.value
    return samples


def ticket(client, who="emma", **body):
    body.setdefault("type", "hardware")
    body.setdefault("title", "Metrics test")
    body.setdefault("description", "Created by a test")
    return client.post("/api/tickets", json=body, headers=auth(who)).json()


def test_empty_system(client):
    m = scrape(client)
    assert m[("helpdesk_tickets_breached_open", ())] == 0
    assert m[("helpdesk_sla_compliance_ratio", ())] == 1.0
    assert m[("helpdesk_auto_resolve_ratio", ())] == 0.0


def test_ticket_metrics(client, app):
    ticket(client, type="password_reset")  # auto-resolves
    hardware = ticket(client)  # stays open
    late = ticket(client, title="Old laptop issue")
    with app.state.session_factory() as session:
        old = session.get(Ticket, late["id"])
        old.due_at = utcnow() - timedelta(hours=1)  # now past due
        session.commit()
    client.patch(
        f"/api/tickets/{hardware['id']}/status", json={"status": "resolved"}, headers=auth("ava")
    )

    m = scrape(client)
    assert m[("helpdesk_tickets_open", (("status", "new"), ("type", "hardware")))] == 1
    assert (
        m[
            (
                "helpdesk_tickets_closed_total",
                (("outcome", "auto_resolved"), ("type", "password_reset")),
            )
        ]
        == 1
    )
    assert (
        m[("helpdesk_tickets_closed_total", (("outcome", "resolved"), ("type", "hardware")))] == 1
    )
    assert m[("helpdesk_tickets_breached_open", ())] == 1
    assert m[("helpdesk_auto_resolve_ratio", ())] == 0.5
    assert m[("helpdesk_sla_compliance_ratio", ())] == 1.0
    assert ("helpdesk_resolution_seconds_median", (("type", "password_reset"),)) in m
    assert m[("helpdesk_audit_events_total", (("action", "password_link.issue"),))] == 1


def test_rejected_tickets_do_not_count_as_resolved(client):
    created = ticket(
        client, type="access_request", title="Need VPN", details={"group": "vpn-users"}
    )
    client.post(
        f"/api/approvals/{created['id']}",
        json={"decision": "reject", "reason": "Office only"},
        headers=auth("liam"),
    )
    m = scrape(client)
    assert (
        m[("helpdesk_tickets_closed_total", (("outcome", "rejected"), ("type", "access_request")))]
        == 1
    )
    assert m[("helpdesk_auto_resolve_ratio", ())] == 0.0


def test_http_metrics_use_route_patterns(client):
    ticket(client)
    client.get("/api/tickets/1", headers=auth("emma"))
    client.get("/api/tickets/999", headers=auth("emma"))
    client.get("/no-such-page")
    m = scrape(client)
    key = (
        "helpdesk_http_requests_total",
        (("method", "GET"), ("route", "/api/tickets/{ticket_id}"), ("status", "200")),
    )
    assert m[key] == 1
    assert (
        m[(key[0], (("method", "GET"), ("route", "/api/tickets/{ticket_id}"), ("status", "404")))]
        == 1
    )
    assert (key[0], (("method", "GET"), ("route", "unmatched"), ("status", "404"))) in m
    # /metrics itself is not counted
    assert not any(k[0] == key[0] and ("route", "/metrics") in k[1] for k in m)


def test_metrics_endpoint_hidden_from_api_docs(client):
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]
