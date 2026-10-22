from collections import Counter

from sqlalchemy import select

from app.demo import load_demo
from app.metrics import TicketCollector
from app.models import AuditEvent, Status, Ticket, TicketType, aware, utcnow


def load(app, tickets=400):
    with app.state.session_factory() as session:
        load_demo(session, tickets=tickets, days=30)
        return session.scalars(select(Ticket)).all()


def test_demo_data_is_realistic(app):
    tickets = load(app)
    assert len(tickets) == 400
    now = utcnow()
    mix = Counter(t.type for t in tickets)
    # Password resets are the biggest group, offboarding the smallest (BRD review).
    assert mix.most_common(1)[0][0] == TicketType.PASSWORD_RESET
    assert min(mix, key=mix.get) == TicketType.OFFBOARDING

    for t in tickets:
        assert t.details["demo"] is True
        assert aware(t.created_at) <= now
        if t.resolved_at is not None:
            assert aware(t.created_at) <= aware(t.resolved_at) <= now
        if t.type == TicketType.OFFBOARDING:
            assert t.status == Status.AUTO_RESOLVED  # worker never picks up demo data
        if t.status == Status.WAITING_APPROVAL:
            assert t.paused_at is not None

    statuses = Counter(t.status for t in tickets)
    assert statuses[Status.AUTO_RESOLVED] > statuses[Status.RESOLVED] / 2
    assert statuses[Status.REJECTED] > 0
    assert any(t.is_breached() for t in tickets if t.type == TicketType.HARDWARE)


def test_demo_writes_audit_events_and_feeds_metrics(app):
    load(app, tickets=150)
    with app.state.session_factory() as session:
        actions = {e.action for e in session.scalars(select(AuditEvent))}
    assert {"user.create", "user.disable", "password_link.issue"} <= actions

    families = {f.name: f for f in TicketCollector(app.state.session_factory).collect()}
    auto_rate = families["helpdesk_auto_resolve_ratio"].samples[0].value
    assert 0.3 < auto_rate < 0.9


def test_demo_is_repeatable(app):
    first = [(t.type, t.title) for t in load(app, tickets=50)]
    with app.state.session_factory() as session:
        load_demo(session, tickets=50, days=30)
        everything = session.scalars(select(Ticket).order_by(Ticket.id)).all()
    second = [(t.type, t.title) for t in everything[50:]]
    assert first == second
