"""SLA rules from docs/sla-policy.md."""

from datetime import datetime, timedelta

from app.models import Priority, TicketType

DEFAULT_PRIORITY: dict[TicketType, Priority] = {
    TicketType.PASSWORD_RESET: Priority.P2,
    TicketType.OFFBOARDING: Priority.P1,
    TicketType.NEW_HIRE: Priority.P3,
    TicketType.ACCESS_REQUEST: Priority.P3,
    TicketType.HARDWARE: Priority.P3,
}

# P1 and P2 are clock hours. P3 and P4 are business days (Mon-Fri).
CLOCK_HOURS = {Priority.P1: 1, Priority.P2: 4}
BUSINESS_DAYS = {Priority.P3: 1, Priority.P4: 3}


def default_priority(ticket_type: TicketType) -> Priority:
    return DEFAULT_PRIORITY[ticket_type]


def add_business_days(start: datetime, days: int) -> datetime:
    """Move forward `days` weekdays, keeping the time of day.

    A ticket opened on a weekend counts from the next Monday.
    """
    current = start
    while current.weekday() >= 5:
        current = (current + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def due_at(priority: Priority, created_at: datetime) -> datetime:
    if priority in CLOCK_HOURS:
        return created_at + timedelta(hours=CLOCK_HOURS[priority])
    return add_business_days(created_at, BUSINESS_DAYS[priority])


def ticket_due_at(
    ticket_type: TicketType, priority: Priority, created_at: datetime, details: dict
) -> datetime:
    """Due time for a ticket, including the date-based rules in the SLA policy."""
    due = due_at(priority, created_at)
    if ticket_type == TicketType.NEW_HIRE:
        # Ready by 9:00 UTC on the start date, if that is sooner than the normal target.
        start = datetime.fromisoformat(details["start_date"]).replace(
            hour=9, tzinfo=created_at.tzinfo
        )
        return max(min(due, start), created_at)
    if ticket_type == TicketType.OFFBOARDING:
        # Access must be gone within 1 hour after the last day ends.
        last_day = datetime.fromisoformat(details["last_day"]).replace(tzinfo=created_at.tzinfo)
        return max(last_day + timedelta(days=1, hours=1), created_at + timedelta(hours=1))
    return due
