"""Business-day targets are counted in the hours the help desk is actually open."""

from datetime import UTC, datetime, timedelta

import pytest

from app import sla
from app.models import Priority, TicketType


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.fixture(autouse=True)
def fixed_holidays(monkeypatch):
    monkeypatch.setenv("HELPDESK_HOLIDAYS", "2026-12-25,2026-01-01")


def test_a_ticket_filed_in_working_hours_starts_now():
    # Wednesday at 11:00
    assert sla.next_working_moment(at("2026-11-18T11:00")) == at("2026-11-18T11:00")


def test_a_ticket_filed_before_the_desk_opens_waits_for_nine():
    assert sla.next_working_moment(at("2026-11-18T06:30")) == at("2026-11-18T09:00")


def test_a_ticket_filed_at_night_starts_the_next_morning():
    assert sla.next_working_moment(at("2026-11-18T23:10")) == at("2026-11-19T09:00")


def test_a_ticket_filed_on_saturday_starts_on_monday():
    assert sla.next_working_moment(at("2026-11-21T10:00")) == at("2026-11-23T09:00")


def test_a_ticket_filed_on_christmas_starts_the_next_open_day():
    # Friday 25 December 2026 is a holiday, so the next open day is Monday the 28th.
    assert sla.next_working_moment(at("2026-12-25T10:00")) == at("2026-12-28T09:00")


def test_closing_time_counts_as_closed():
    assert sla.next_working_moment(at("2026-11-18T17:00")) == at("2026-11-19T09:00")


def test_one_business_day_from_a_normal_afternoon():
    assert sla.due_at(Priority.P3, at("2026-11-18T14:00")) == at("2026-11-19T14:00")


def test_one_business_day_from_friday_afternoon_lands_on_monday():
    assert sla.due_at(Priority.P3, at("2026-11-20T14:00")) == at("2026-11-23T14:00")


def test_a_business_day_skips_a_holiday():
    # Wednesday 23 December, one working day on, is Thursday the 24th; but a ticket
    # filed on the 24th is due on Monday the 28th because Friday is closed.
    assert sla.due_at(Priority.P3, at("2026-12-24T10:00")) == at("2026-12-28T10:00")


def test_three_business_days_skip_the_weekend_and_a_holiday():
    """From Wednesday the 23rd: Thursday the 24th, then Christmas and the weekend are
    skipped, so Monday the 28th and Tuesday the 29th finish the count."""
    assert sla.due_at(Priority.P4, at("2026-12-23T10:00")) == at("2026-12-29T10:00")


def test_an_overnight_ticket_is_due_the_working_day_after_it_opens():
    """Filed at 23:10 Wednesday: the day starts at 09:00 Thursday and is due Friday."""
    assert sla.due_at(Priority.P3, at("2026-11-18T23:10")) == at("2026-11-20T09:00")


@pytest.mark.parametrize("priority, hours", [(Priority.P1, 1), (Priority.P2, 4)])
def test_urgent_tickets_ignore_business_hours(priority, hours):
    night = at("2026-11-21T02:00")  # Saturday, middle of the night
    assert sla.due_at(priority, night) == night + timedelta(hours=hours)


def test_holidays_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("HELPDESK_HOLIDAYS", "2026-11-19")
    assert not sla.is_working_day(at("2026-11-19T10:00").date())
    assert sla.is_working_day(at("2026-12-25T10:00").date())  # no longer listed


def test_a_typo_in_the_holiday_list_does_not_break_the_clock(monkeypatch):
    monkeypatch.setenv("HELPDESK_HOLIDAYS", "2026-11-19,not-a-date,  2026-11-20 ")
    assert sla.holidays() == {at("2026-11-19T00:00").date(), at("2026-11-20T00:00").date()}


def test_a_new_hire_is_still_due_by_nine_on_the_start_date():
    """The date rules in the policy still win over the business-day target."""
    due = sla.ticket_due_at(
        TicketType.NEW_HIRE, Priority.P3, at("2026-11-18T10:00"), {"start_date": "2026-11-19"}
    )
    assert due == at("2026-11-19T09:00")
