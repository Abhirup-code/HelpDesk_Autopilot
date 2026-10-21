"""Prometheus metrics.

Ticket numbers are read from the database each time Prometheus scrapes, so
they are always correct, even with several API processes or after a restart.
HTTP request counts and latency are measured per process.
"""

import time
from collections.abc import Iterator
from statistics import median

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.exposition import generate_latest
from prometheus_client.registry import Collector
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import CLOSED_STATUSES, AuditEvent, Status, Ticket, aware, utcnow


class TicketCollector(Collector):
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory

    def collect(self) -> Iterator:
        now = utcnow()
        with self.factory() as session:
            tickets = session.scalars(select(Ticket)).all()
            audit_counts = session.execute(
                select(AuditEvent.action, func.count()).group_by(AuditEvent.action)
            ).all()

        open_tickets = GaugeMetricFamily(
            "helpdesk_tickets_open",
            "Tickets not yet resolved, by type and status",
            labels=["type", "status"],
        )
        closed = CounterMetricFamily(
            "helpdesk_tickets_closed",
            "Tickets closed, by type and how they closed",
            labels=["type", "outcome"],
        )
        breached_open = GaugeMetricFamily(
            "helpdesk_tickets_breached_open", "Open tickets already past their due time"
        )
        compliance = GaugeMetricFamily(
            "helpdesk_sla_compliance_ratio", "Share of resolved tickets closed on time"
        )
        auto_rate = GaugeMetricFamily(
            "helpdesk_auto_resolve_ratio", "Share of closed tickets resolved by automation"
        )
        median_seconds = GaugeMetricFamily(
            "helpdesk_resolution_seconds_median",
            "Median time from creation to resolution, by type",
            labels=["type"],
        )
        audit = CounterMetricFamily(
            "helpdesk_audit_events", "Audit log entries, by action", labels=["action"]
        )

        open_counts: dict[tuple[str, str], int] = {}
        closed_counts: dict[tuple[str, str], int] = {}
        durations: dict[str, list[float]] = {}
        on_time = resolved = auto = breached = 0
        for t in tickets:
            if t.status in CLOSED_STATUSES:
                key = (t.type.value, t.status.value)
                closed_counts[key] = closed_counts.get(key, 0) + 1
                if t.status == Status.REJECTED:
                    continue
                resolved += 1
                auto += t.status == Status.AUTO_RESOLVED
                on_time += not t.is_breached()
                seconds = (aware(t.resolved_at) - aware(t.created_at)).total_seconds()
                durations.setdefault(t.type.value, []).append(seconds)
            else:
                key = (t.type.value, t.status.value)
                open_counts[key] = open_counts.get(key, 0) + 1
                breached += t.is_breached(now)

        for (ticket_type, status), count in sorted(open_counts.items()):
            open_tickets.add_metric([ticket_type, status], count)
        for (ticket_type, outcome), count in sorted(closed_counts.items()):
            closed.add_metric([ticket_type, outcome], count)
        for ticket_type, values in sorted(durations.items()):
            median_seconds.add_metric([ticket_type], median(values))
        for action, count in sorted(audit_counts):
            audit.add_metric([action], count)
        breached_open.add_metric([], breached)
        compliance.add_metric([], on_time / resolved if resolved else 1.0)
        auto_rate.add_metric([], auto / resolved if resolved else 0.0)

        yield from (
            open_tickets,
            closed,
            breached_open,
            compliance,
            auto_rate,
            median_seconds,
            audit,
        )


def install_metrics(app: FastAPI, factory: sessionmaker[Session]) -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(TicketCollector(factory))
    requests = Counter(
        "helpdesk_http_requests",
        "HTTP requests handled",
        ["method", "route", "status"],
        registry=registry,
    )
    latency = Histogram(
        "helpdesk_http_request_seconds",
        "HTTP request latency",
        ["method", "route"],
        registry=registry,
    )

    @app.middleware("http")
    async def measure(request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        # Use the route pattern (/api/tickets/{ticket_id}) so IDs don't create
        # a new time series for every ticket.
        route = getattr(request.scope.get("route"), "path", "unmatched")
        if route != "/metrics":
            requests.labels(request.method, route, str(response.status_code)).inc()
            latency.labels(request.method, route).observe(time.perf_counter() - start)
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    return registry
