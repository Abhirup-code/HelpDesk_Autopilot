# SLA Policy

The SLA clock starts when a ticket is created and stops when it is resolved. Time spent in `Waiting approval` is paused, because IT cannot act until the manager decides.

## Targets by priority

| Priority | Meaning | Resolve within |
|---|---|---|
| P1 Critical | User cannot work at all, or security risk | 1 hour |
| P2 High | Major impact, no workaround | 4 hours |
| P3 Normal | Impact with a workaround | 1 business day |
| P4 Low | Request or question | 3 business days |

## Business hours

The help desk is open 09:00 to 17:00 UTC, Monday to Friday, and closed on the dates in `HELPDESK_HOLIDAYS` (a comma-separated list of `YYYY-MM-DD`, defaulting to the usual US public holidays).

A business-day target starts counting from the next moment the desk is open. A P3 raised at 23:10 on Wednesday starts at 09:00 on Thursday and is due at 09:00 on Friday, rather than being due at 23:10 on Thursday when nobody is there.

P1 and P2 ignore business hours entirely. Something urgent is urgent at two in the morning.

## Default priority by ticket type

| Ticket type | Default priority | Notes |
|---|---|---|
| Password reset / lockout | P2 | Usually auto-resolved within minutes |
| Offboarding | P1 on the last day | Security risk if missed |
| New hire | P3 | Due by the start date if that is sooner |
| Access request | P3 | Clock paused while waiting for approval |
| Hardware / other | P3 | Always handled by IT |

## Statuses

| Status | SLA clock |
|---|---|
| New | Running |
| In progress | Running |
| Waiting approval | Paused |
| Waiting IT approval | Running |
| Resolved / Auto-resolved | Stopped |
| Breached | Set when due time passes before resolution |

## Dashboard metrics

| Metric | Definition |
|---|---|
| Open tickets | Tickets not Resolved or Auto-resolved |
| Median time to resolve | Median of (resolved time minus the time work could start). Offboarding can start when the last day ends; access requests include manager wait time |
| SLA compliance | Resolved within due time divided by all resolved |
| Auto-resolve rate | Auto-resolved divided by all resolved |
| Breaches | Tickets currently or ever marked Breached |
