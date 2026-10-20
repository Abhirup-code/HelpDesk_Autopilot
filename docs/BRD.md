# Business Requirements Document: HelpDesk Autopilot

| Field | Value |
|---|---|
| Author | Abhirup Khan |
| Version | 1.0 |
| Status | Approved for build |
| Date | October 2026 |

## 1. Background

The example company is a 150-person firm with a two-person IT team. IT receives about 25 tickets a day by email and chat. A review of 30 days of tickets showed:

| Ticket type | Share of tickets | Avg. time to resolve |
|---|---|---|
| Password reset / MFA lockout | 38% | 2.5 hours |
| Access request | 22% | 1 day |
| New hire setup | 12% | 2 days |
| Offboarding | 6% | 4 days |
| Hardware / other | 22% | 1.5 days |

Two-thirds of the work is routine and rule-based, but it is all done by hand.

## 2. Problem statement

Routine account work takes most of the IT team's time, leaves no audit trail, and creates security risk when departing employees keep access. Management has no visibility into response times.

## 3. Objectives

| ID | Objective | Measure | Target |
|---|---|---|---|
| OBJ-1 | Cut time spent on routine tickets | % of tickets auto-resolved | 50% or more |
| OBJ-2 | Faster password resets | Median time to resolve | Under 10 minutes |
| OBJ-3 | Close the offboarding gap | Accounts disabled by end of last day | 100% |
| OBJ-4 | Make every access change traceable | Changes with an audit record | 100% |
| OBJ-5 | Give management SLA visibility | Live dashboard available | Yes |

## 4. Scope

**In scope**
- Web portal to submit and track tickets
- Automated password reset, new-hire setup and offboarding against an LDAP directory
- Manager approval for access requests
- Audit log of every directory change
- SLA tracking and a Grafana dashboard
- Bulk onboarding from a CSV file

**Out of scope (version 1)**
- Hardware ordering and asset management
- Integration with a real Microsoft Entra ID tenant (LDAP stands in for it)
- Email delivery (messages go to an in-app inbox for the demo)
- Mobile app

## 5. Stakeholders

| Role | Interest |
|---|---|
| Employee | Fast, simple way to get help |
| Manager | Approves access for their team, wants new hires ready on day one |
| IT agent | Fewer repetitive tickets, clear queue |
| IT manager | SLA reporting, audit evidence |
| Security / compliance | Proof that access is approved and removed on time |

## 6. Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-1 | Users can submit a ticket with type, description and priority | Must |
| FR-2 | Users can see the status of their own tickets | Must |
| FR-3 | Password reset tickets generate a single-use link that expires in 15 minutes | Must |
| FR-4 | New-hire tickets create a directory account and add default groups for the department | Must |
| FR-5 | Offboarding tickets disable the account and remove all groups on the scheduled date | Must |
| FR-6 | Access requests require approval from the requester's manager before any change | Must |
| FR-7 | Requests for admin or privileged groups always need a manual IT approval as well | Must |
| FR-8 | Every directory change is written to an append-only audit log (who, what, when, ticket ID) | Must |
| FR-9 | Each ticket has an SLA due time based on type and priority | Must |
| FR-10 | Tickets that pass their due time are marked Breached | Must |
| FR-11 | IT agents see a queue they can filter by status, type and priority | Should |
| FR-12 | IT can bulk-create accounts from a CSV file | Should |
| FR-13 | A Grafana dashboard shows open tickets, resolution time, breaches and auto-resolve rate | Should |
| FR-14 | Failed automations reopen the ticket and assign it to IT | Must |

## 7. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Security: no passwords stored in plain text, secrets only from environment variables |
| NFR-2 | Security: least privilege, the app's directory account can only manage the Users OU |
| NFR-3 | Reliability: a failed automation never leaves an account half-created |
| NFR-4 | Quality: 90% or higher automated test coverage, enforced in CI |
| NFR-5 | Performance: portal pages load in under 1 second with 10,000 tickets |
| NFR-6 | Portability: the whole system starts with one `docker compose up` command |

## 8. Assumptions and constraints

- Each employee has exactly one manager in the directory.
- Department determines default groups (for example, Finance gets `finance-share` and `erp-users`).
- The project runs locally at no cost, so cloud services are not used.

## 9. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Automation grants the wrong access | High | Manager approval, privileged groups need IT sign-off, full audit log |
| Reset link is intercepted | High | Single use, 15-minute expiry, token stored only as a hash |
| Directory is unavailable | Medium | Ticket reopens and goes to IT (FR-14) |
| Users bypass the portal | Low | Dashboard shows volume, so leadership can push adoption |

## 10. Success criteria

The project is successful when the demo data shows the targets in section 3 being met on the dashboard, and all Must requirements pass their acceptance tests in CI.
