from datetime import timedelta

from app import worker
from app.automation import today_utc
from app.models import Status, Ticket, TicketType, User, utcnow


def test_worker_runs_due_offboardings(app, directory):
    factory = app.state.session_factory
    with factory() as session:
        liam = session.query(User).filter_by(username="liam").one()
        yesterday = (today_utc() - timedelta(days=1)).isoformat()
        session.add(
            Ticket(
                type=TicketType.OFFBOARDING,
                priority="P1",
                status=Status.NEW,
                title="Offboard Emma",
                description="Last day was yesterday",
                requester_id=liam.id,
                due_at=utcnow(),
                details={"username": "emma", "last_day": yesterday},
            )
        )
        session.commit()

    assert worker.run_once(factory, directory, "http://test") == 1
    assert directory.is_disabled("emma")
    assert worker.run_once(factory, directory, "http://test") == 0
