"""SLA rules from docs/sla-policy.md."""

import os
from datetime import date, datetime, time, timedelta

from app.models import Priority, TicketType

# The working day a business-day target is counted in. P1 and P2 ignore all of this:
# something urgent is urgent at two in the morning.
WORK_START = time(9, 0)
WORK_END = time(17, 0)

# Days the help desk is closed, on top of weekends. Set HELPDESK_HOLIDAYS to a
# comma-separated list of YYYY-MM-DD dates to match your own calendar.
DEFAULT_HOLIDAYS = (
    "2026-01-01",  # New Year's Day
    "2026-05-25",  # Memorial Day
    "2026-07-03",  # Independence Day, observed
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-11-27",  # day after Thanksgiving
    "2026-12-25",  # Christmas Day
)


def holidays() -> frozenset[date]:
    """The closed days, from the environment when it says so."""
    raw = os.environ.get("HELPDESK_HOLIDAYS")
    values = [v.strip() for v in raw.split(",")] if raw else list(DEFAULT_HOLIDAYS)
    days = set()
    for value in values:
        try:
            days.add(date.fromisoformat(value))
        except ValueError:
            # A typo in one date should not take the SLA clock down with it.
            continue
    return frozenset(days)


def is_working_day(day: date) -> bool:
    return day.weekday() < 5 and day not in holidays()


def next_working_moment(moment: datetime) -> datetime:
    """The first time the help desk is open at or after this moment.

    A ticket filed at eleven at night is not ignored for ten hours and then due at
    eleven the next night: its day starts when the desk opens.
    """
    current = moment
    for _ in range(400):  # a year of closed days is more than enough
        if not is_working_day(current.date()):
            current = datetime.combine(
                current.date() + timedelta(days=1), WORK_START, tzinfo=current.tzinfo
            )
            continue
        if current.timetz().replace(tzinfo=None) < WORK_START:
            return datetime.combine(current.date(), WORK_START, tzinfo=current.tzinfo)
        if current.timetz().replace(tzinfo=None) >= WORK_END:
            current = datetime.combine(
                current.date() + timedelta(days=1), WORK_START, tzinfo=current.tzinfo
            )
            continue
        return current
    return current


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
    """Move forward `days` open days, keeping the time of day.

    A ticket filed when the desk is shut counts from the next time it opens, so a
    weekend, an evening and a public holiday all behave the same way.
    """
    current = next_working_moment(start)
    added = 0
    while added < days:
        current += timedelta(days=1)
        if is_working_day(current.date()):
            added += 1
    return current


def due_at(priority: Priority, created_at: datetime) -> datetime:
    if priority in CLOCK_HOURS:
        # Urgent is urgent at two in the morning: no business hours here.
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
