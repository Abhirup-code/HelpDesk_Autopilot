from datetime import UTC, datetime

import pytest

from app.models import Priority, Ticket, TicketType
from app.sla import add_business_days, default_priority, due_at

MON_10AM = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
FRI_4PM = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)
SAT_NOON = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("ticket_type", "expected"),
    [
        (TicketType.PASSWORD_RESET, Priority.P2),
        (TicketType.OFFBOARDING, Priority.P1),
        (TicketType.NEW_HIRE, Priority.P3),
        (TicketType.ACCESS_REQUEST, Priority.P3),
        (TicketType.HARDWARE, Priority.P3),
    ],
)
def test_default_priority_follows_policy(ticket_type, expected):
    assert default_priority(ticket_type) == expected


def test_p1_is_due_in_one_hour():
    assert due_at(Priority.P1, MON_10AM) == datetime(2026, 10, 19, 11, 0, tzinfo=UTC)


def test_p2_is_due_in_four_hours():
    assert due_at(Priority.P2, MON_10AM) == datetime(2026, 10, 19, 14, 0, tzinfo=UTC)


def test_p3_is_next_business_day():
    assert due_at(Priority.P3, MON_10AM) == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def test_p3_on_friday_skips_weekend():
    assert due_at(Priority.P3, FRI_4PM) == datetime(2026, 10, 26, 16, 0, tzinfo=UTC)


def test_p4_is_three_business_days():
    assert due_at(Priority.P4, FRI_4PM) == datetime(2026, 10, 28, 16, 0, tzinfo=UTC)


def test_weekend_ticket_counts_from_monday_morning():
    assert add_business_days(SAT_NOON, 1) == datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


def _ticket(due, resolved=None):
    return Ticket(due_at=due, resolved_at=resolved)


def test_open_ticket_past_due_is_breached():
    assert _ticket(MON_10AM).is_breached(now=FRI_4PM)


def test_open_ticket_before_due_is_not_breached():
    assert not _ticket(FRI_4PM).is_breached(now=MON_10AM)


def test_resolved_late_is_breached():
    assert _ticket(MON_10AM, resolved=FRI_4PM).is_breached()


def test_resolved_on_time_is_not_breached():
    assert not _ticket(FRI_4PM, resolved=MON_10AM).is_breached()


def test_naive_datetimes_are_treated_as_utc():
    naive_due = datetime(2026, 10, 19, 10, 0)
    assert _ticket(naive_due).is_breached(now=FRI_4PM)
