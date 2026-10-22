"""Load 30 days of realistic demo tickets for the dashboard.

Run after seeding users:
    docker compose exec api python -m app.demo

The mix of ticket types follows the 30-day review in docs/BRD.md. Demo tickets
are marked with "demo": true in their details. They are written straight to the
database and do not touch the directory.
"""

import argparse
import random
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import sla
from app.db import init_db, make_engine, make_session_factory
from app.models import AuditEvent, Priority, Role, Status, Ticket, TicketType, User, utcnow

# Share of tickets by type, from the BRD's 30-day ticket review.
TYPE_MIX = {
    TicketType.PASSWORD_RESET: 38,
    TicketType.ACCESS_REQUEST: 22,
    TicketType.NEW_HIRE: 12,
    TicketType.OFFBOARDING: 6,
    TicketType.HARDWARE: 22,
}
HARDWARE_ISSUES = [
    "Laptop will not charge",
    "Monitor flickering",
    "Docking station not detected",
    "Printer jam on 3rd floor",
    "Headset microphone not working",
    "Keyboard keys sticking",
    "Laptop running very slow",
    "Need a second monitor",
]
GROUPS = ["tableau-users", "vpn-users", "erp-users", "finance-share", "git-users", "it-admins"]
FIRST = ["Maya", "Leo", "Priya", "Sam", "Nora", "Omar", "Grace", "Diego", "Hana", "Ivan"]
LAST = ["Brooks", "Singh", "Lopez", "Nguyen", "Okafor", "Meyer", "Rossi", "Kim", "Shah"]
DEPARTMENTS = ["Engineering", "Finance", "Operations", "Sales"]


class DemoBuilder:
    def __init__(self, session: Session, rng: random.Random, now: datetime) -> None:
        self.session, self.rng, self.now = session, rng, now
        users = session.scalars(select(User)).all()
        if not users:
            raise SystemExit("No users yet. Run `python -m app.seed` first.")
        self.employees = [u for u in users if u.manager_id is not None] or users
        self.leads = [u for u in users if u.role in (Role.MANAGER, Role.AGENT)] or users

    # -- helpers ----------------------------------------------------------

    def created_time(self, days: int) -> datetime:
        """A moment in the last `days` days, mostly on weekday working hours."""
        while True:
            when = self.now - timedelta(seconds=self.rng.uniform(0, days * 86400))
            weekday = when.weekday() < 5
            work_hours = 13 <= when.hour < 23  # 8am to 6pm Chicago time, in UTC
            if self.rng.random() < (0.9 if weekday and work_hours else 0.08):
                return when

    def audit(self, ticket: Ticket, actor: str, action: str, target: str, at: datetime) -> None:
        self.session.add(
            AuditEvent(
                at=at, actor=actor, action=action, target=target, ticket_id=ticket.id, detail="demo"
            )
        )

    def close(self, ticket: Ticket, status: Status, after: timedelta) -> None:
        when = ticket.created_at + after
        if when >= self.now:
            # Not finished yet: leave it open, as a real queue would be.
            ticket.status = Status.IN_PROGRESS if status != Status.AUTO_RESOLVED else Status.NEW
            return
        ticket.status, ticket.resolved_at = status, when

    # -- one ticket of each type -----------------------------------------------

    def build(self, ticket_type: TicketType, created: datetime) -> Ticket:
        priority = sla.default_priority(ticket_type)
        details: dict = {"demo": True}
        requester = self.rng.choice(self.employees)
        title, description = ticket_type.value.replace("_", " ").title(), "Demo ticket"

        if ticket_type == TicketType.NEW_HIRE:
            requester = self.rng.choice(self.leads)
            first, last = self.rng.choice(FIRST), self.rng.choice(LAST)
            start = (created + timedelta(days=self.rng.randint(3, 14))).date()
            details |= {
                "first_name": first,
                "last_name": last,
                "department": self.rng.choice(DEPARTMENTS),
                "start_date": start.isoformat(),
                "username": f"{first[0]}{last}".lower(),
            }
            title = f"New hire: {first} {last}"
        elif ticket_type == TicketType.OFFBOARDING:
            requester = self.rng.choice(self.leads)
            # Always in the past, so the worker never tries to run a demo offboarding.
            last_day = min(created.date() + timedelta(days=self.rng.randint(0, 5)), self.yesterday)
            details |= {"username": f"former{self.rng.randint(100, 999)}"}
            details["last_day"] = last_day.isoformat()
            title = "Offboarding"
        elif ticket_type == TicketType.ACCESS_REQUEST:
            details["group"] = self.rng.choice(GROUPS)
            title = f"Need {details['group']}"
        elif ticket_type == TicketType.HARDWARE:
            title = self.rng.choice(HARDWARE_ISSUES)
            priority = self.rng.choices(
                [Priority.P2, Priority.P3, Priority.P4], weights=[15, 70, 15]
            )[0]

        ticket = Ticket(
            type=ticket_type,
            priority=priority,
            status=Status.NEW,
            title=title,
            description=description,
            requester_id=requester.id,
            created_at=created,
            due_at=sla.ticket_due_at(ticket_type, priority, created, details),
            details=details,
        )
        self.session.add(ticket)
        self.session.flush()
        getattr(self, f"_{ticket_type.value}")(ticket, requester)
        return ticket

    def _password_reset(self, ticket: Ticket, requester: User) -> None:
        if self.rng.random() < 0.95:
            self.close(ticket, Status.AUTO_RESOLVED, timedelta(seconds=self.rng.uniform(0.1, 2)))
            self.audit(
                ticket, "autopilot", "password_link.issue", requester.username, ticket.created_at
            )
        else:  # automation failed, IT fixed it by hand
            ticket.automation_error = "Directory did not respond"
            self.close(ticket, Status.RESOLVED, timedelta(hours=self.rng.uniform(1, 6)))

    def _new_hire(self, ticket: Ticket, requester: User) -> None:
        username = ticket.details["username"]
        if self.rng.random() < 0.92:
            self.close(ticket, Status.AUTO_RESOLVED, timedelta(seconds=self.rng.uniform(0.1, 1)))
            self.audit(ticket, "autopilot", "user.create", username, ticket.created_at)
        else:
            ticket.automation_error = "Group add failed; account rolled back"
            self.close(ticket, Status.RESOLVED, timedelta(hours=self.rng.uniform(2, 20)))

    def _offboarding(self, ticket: Ticket, requester: User) -> None:
        end_of_last_day = datetime.combine(
            date.fromisoformat(ticket.details["last_day"]) + timedelta(days=1),
            datetime.min.time(),
            tzinfo=ticket.created_at.tzinfo,
        )
        runs_at = max(end_of_last_day, ticket.created_at) + timedelta(
            seconds=self.rng.uniform(1, 60)
        )
        # Never leave a demo offboarding open, or the worker would try to run it.
        runs_at = max(min(runs_at, self.now), ticket.created_at)
        ticket.status, ticket.resolved_at = Status.AUTO_RESOLVED, runs_at
        self.audit(ticket, "autopilot", "user.disable", ticket.details["username"], runs_at)

    def _access_request(self, ticket: Ticket, requester: User) -> None:
        manager = self.session.get(User, requester.manager_id) if requester.manager_id else None
        approver = manager.username if manager else "ava"
        # Time waiting for the manager doesn't count against the SLA.
        wait = timedelta(minutes=self.rng.lognormvariate(4.5, 1.0))
        if ticket.created_at + wait >= self.now:
            ticket.status = Status.WAITING_APPROVAL
            ticket.paused_at = ticket.created_at
            return
        ticket.due_at = ticket.due_at + wait
        approved_at = ticket.created_at + wait
        if self.rng.random() < 0.12:
            details = {**ticket.details, "rejection_reason": "Not needed for this role"}
            ticket.details = details
            self.close(ticket, Status.REJECTED, wait)
            self.audit(ticket, approver, "approval.reject", requester.username, approved_at)
            return
        self.audit(ticket, approver, "approval.manager", requester.username, approved_at)
        self.close(ticket, Status.RESOLVED, wait + timedelta(seconds=1))
        self.audit(ticket, "autopilot", "group.add", requester.username, approved_at)

    def _hardware(self, ticket: Ticket, requester: User) -> None:
        # Hands-on work: most fixed within the day, some take longer.
        hours = self.rng.lognormvariate(1.6, 0.9)
        self.close(ticket, Status.RESOLVED, timedelta(hours=hours))

    @property
    def yesterday(self) -> date:
        return (self.now - timedelta(days=1)).date()


def load_demo(session: Session, tickets: int = 500, days: int = 30, seed: int = 42) -> int:
    rng = random.Random(seed)  # noqa: S311 (demo data, not security)
    builder = DemoBuilder(session, rng, utcnow())
    types, weights = zip(*TYPE_MIX.items(), strict=True)
    for _ in range(tickets):
        builder.build(rng.choices(types, weights)[0], builder.created_time(days))
    session.commit()
    return tickets


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tickets", type=int, default=500)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    engine = make_engine()
    init_db(engine)
    with make_session_factory(engine)() as session:
        count = load_demo(session, args.tickets, args.days)
    print(f"Loaded {count} demo tickets over the last {args.days} days.")


if __name__ == "__main__":  # pragma: no cover
    main()
