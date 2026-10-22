# HelpDesk Autopilot

[![CI](https://github.com/Abhirup-code/HelpDesk_Autopilot/actions/workflows/ci.yml/badge.svg)](https://github.com/Abhirup-code/HelpDesk_Autopilot/actions/workflows/ci.yml)

A self-service IT help desk that automates the most common tickets (new-hire setup, offboarding and password resets) against a company directory, with manager approvals, a full audit trail and a live SLA dashboard.

> **Status:** in development. See the [roadmap](#roadmap).

## Screenshots

**SLA dashboard (Grafana):** live view of open tickets, SLA compliance, auto-resolve rate and trends over 30 days.

<img width="1462" height="821" alt="Screenshot 2026-10-22 at 1 55 37 AM" src="https://github.com/user-attachments/assets/411225f6-ad03-43a5-b4cc-77e6ebc31b0d" />


**Alert rules (Prometheus):** fires when the API is down, tickets breach their SLA, compliance drops below 90%, or an automation fails.

<img width="1462" height="821" alt="Screenshot 2026-10-22 at 1 56 14 AM" src="https://github.com/user-attachments/assets/506cebd4-9ffa-4a13-9aa1-dc28e3668258" />


**Approvals:** admin rights need both the manager and IT, and nobody can approve their own request.

<img width="580" height="370" alt="Screenshot 2026-10-22 at 1 58 44 AM" src="https://github.com/user-attachments/assets/86a84fd8-612b-42ac-afef-aa71c9819b26" />


**Tamper-proof audit log:** the database itself refuses to delete audit records.

<img width="580" height="370" alt="Screenshot 2026-10-22 at 1 58 09 AM" src="https://github.com/user-attachments/assets/325b2e4e-9470-4b85-ab71-9e11e7a85039" />

## The problem

At small and mid-size companies, one or two IT people handle every request by hand:

- Password resets and new-hire setups eat most of the day.
- There is no clear record of who changed whose access, or when.
- Offboarding gets missed, so former employees keep working accounts.
- Nobody can say how long tickets take, or which ones break the SLA.

## The solution

HelpDesk Autopilot turns routine tickets into automated, approved and logged workflows.

| Ticket type | Today (manual) | With Autopilot |
|---|---|---|
| Password reset | IT verifies the user and resets by hand | One-time reset link, expires in 15 minutes |
| New hire | IT creates the account and adds groups from an email | Account and groups created from the request |
| Offboarding | Often delayed or forgotten | Account disabled and groups removed on the last day |
| Access request | Approved in chat, no record | Manager approval required, logged |

## Architecture

```mermaid
flowchart LR
    U[Employee / Manager] --> P[Web portal]
    A[IT agent] --> P
    P --> API[FastAPI service]
    API --> DB[(PostgreSQL<br/>tickets + audit log)]
    API --> W[Automation worker]
    W --> L[(OpenLDAP directory)]
    W --> DB
    API -- /metrics --> PR[Prometheus]
    PR --> G[Grafana SLA dashboard]
```

## Tech stack

| Area | Tools |
|---|---|
| App | Python, FastAPI, PostgreSQL |
| Directory | OpenLDAP (stands in for Active Directory / Entra ID) |
| Scripts | Python, PowerShell (bulk onboarding from CSV) |
| Testing | pytest, Playwright, 90% coverage gate |
| DevOps | Docker Compose, GitHub Actions, Trivy, CodeQL |
| Monitoring | Prometheus, Grafana |

## Roadmap

- [x] Requirements: BRD, user stories, process maps, SLA policy
- [x] Ticket API with PostgreSQL and tests
- [x] Directory automation: onboarding, offboarding, password reset
- [x] Manager approvals and audit log
- [ ] Web portal with Playwright tests
- [x] CI pipeline with security scans
- [x] Prometheus metrics and Grafana SLA dashboard
- [x] Demo data, screenshots and walkthrough

## Run it locally

You need Docker Desktop. Run these from the project folder:

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec api python -m app.seed
```

The last command gives each demo user a directory account and prints their API token:

| User | Role | Department |
|---|---|---|
| emma | Employee | Finance |
| noah | Employee | Engineering |
| liam | Manager | Finance |
| ava | IT agent | IT |

Open http://127.0.0.1:8000/docs to try the API in your browser. Click **Authorize** and paste a token.

Or use curl:

```bash
curl -X POST http://127.0.0.1:8000/api/tickets \
  -H "Authorization: Bearer <emma's token>" \
  -H "Content-Type: application/json" \
  -d '{"type":"password_reset","title":"Locked out of email","description":"MFA keeps failing"}'
```

## SLA dashboard

After the stack is up and seeded, load 30 days of demo tickets:

```bash
docker compose exec api python -m app.demo
```

Then open:

| What | Where |
|---|---|
| Grafana SLA dashboard | http://127.0.0.1:3000 (opens straight to the dashboard, no sign-in needed to view) |
| Prometheus and alert rules | http://127.0.0.1:9090/alerts |
| Raw metrics | http://127.0.0.1:8000/metrics |

The dashboard shows:

- **Right now:** open tickets, tickets breaching SLA, approvals waiting, SLA compliance, auto-resolve rate, median time to resolve
- **Trends:** tickets per day by type, and SLA compliance per day against the 90% target
- **Where the time goes:** median time to resolve by ticket type, and outcomes
- **Live from Prometheus:** API requests per second, 95th percentile latency, alerts firing
- **Audit trail:** the latest directory changes and approvals

Grafana reads ticket history from PostgreSQL with a read-only login that can only see `tickets` and `audit_events`. Live numbers come from Prometheus, which scrapes the API every 15 seconds.

**Alerts** (in `monitoring/prometheus/alerts.yml`, unit tested with promtool): API down, open tickets past their SLA, SLA compliance below 90%, and automation failures.

Demo tickets follow the ticket mix from the BRD's 30-day review and are marked `"demo": true`. They never touch the directory.

## What the automations do

| Ticket type | Who can file it | What happens |
|---|---|---|
| `password_reset` | Anyone | A one-time link (15 minutes, works once) goes to your inbox. Ticket auto-resolves |
| `new_hire` | Manager or IT | Directory account created with the department's groups. The manager gets the username and a 72-hour set-password link |
| `offboarding` | Manager or IT | After the last day ends: all groups removed, password removed, account moved to `ou=Disabled` |
| `access_request` | Anyone | Goes to your manager. `it-admins` also needs an IT agent. Approved groups are added automatically |
| `hardware` | Anyone | Goes to the IT queue |

Every directory change is written to the audit log. If an automation fails, completed steps are undone, the ticket moves to `in_progress` with the error, and IT picks it up.

**Example: new hire** (as Liam)

```json
{
  "type": "new_hire",
  "title": "New financial analyst",
  "description": "Starts next Monday",
  "details": {"first_name": "Jane", "last_name": "Doe", "department": "Finance", "start_date": "2026-10-26"}
}
```

Departments: Engineering, Finance, IT, Operations, Sales.

**Example: offboarding** (as Liam)

```json
{
  "type": "offboarding",
  "title": "Noah's last day",
  "description": "Moving to another company",
  "details": {"username": "noah", "last_day": "2026-10-19"}
}
```

A past last day runs right away. A future one waits for the worker, which checks every minute.

**Example: access request** (as Emma)

```json
{
  "type": "access_request",
  "title": "Need Tableau",
  "description": "For month-end reports",
  "details": {"group": "tableau-users"}
}
```

Liam sees it at `GET /api/approvals` and answers with `POST /api/approvals/{id}`:

```json
{"decision": "approve"}
```

or

```json
{"decision": "reject", "reason": "Use the shared dashboard instead"}
```

Requestable groups: erp-users, finance-share, git-users, helpdesk-agents, it-admins, tableau-users, vpn-users.

While a request waits for the manager, the SLA clock is paused, because IT can't act yet.

## Security choices

- The app signs in to the directory with a service account that can only manage `ou=Users`, `ou=Groups` and `ou=Disabled`. It cannot change anything else.
- Passwords are set with the LDAP password modify operation, so the directory hashes them with Argon2id. The app never stores passwords.
- API tokens and password links are random 256-bit values, stored only as SHA-256 hashes.
- Password links give one generic error for wrong, used or expired links, so they can't be probed.
- LDAP search filters and names are escaped against injection.
- Separation of duties: nobody can approve their own access request, and admin rights always need both a manager and an IT agent.
- The audit log is append-only. The API has no way to change it, and database triggers refuse any UPDATE or DELETE on it.
- Blocked attempts are logged too: trying to approve your own request leaves an `approval.denied` record.

See [SECURITY.md](SECURITY.md) for the full list of controls.

## CI pipeline

Every pull request runs:

| Check | What it does |
|---|---|
| Lint (ruff) | Style, bug and security rules |
| Tests (sqlite), Tests (postgres) | 130+ tests with a 90% coverage gate, on both databases, including every dashboard query |
| Monitoring config | promtool checks the Prometheus config and unit tests the alert rules |
| Build and scan images | Builds both images, checks they run as non-root, and fails on fixable HIGH or CRITICAL vulnerabilities (Trivy) |
| End-to-end (Docker Compose) | Starts the whole stack and tests password reset, new hire, approvals, self-approval block, audit log protection, Prometheus and Grafana |
| Secret scanning (gitleaks) | Scans the full git history |
| Dependency audit (pip-audit), Dependency review | Known vulnerabilities in Python packages |
| CodeQL | Code security analysis for Python and the workflows |
| Workflow lint (zizmor) | Security issues in GitHub Actions |

Dependabot opens weekly update PRs for Python packages, Docker images and actions.

Run the end-to-end test on your own machine with `scripts/e2e.sh`.

## API

| Method | Path | Who | What |
|---|---|---|---|
| GET | `/health` | Anyone | Health check |
| POST | `/api/tickets` | Signed in | Create a ticket. Automations run right away |
| GET | `/api/tickets` | Signed in | Employees see their own tickets, IT agents see all. Filter with `status`, `type`, `priority` |
| GET | `/api/tickets/{id}` | Owner or agent | One ticket. Other people's tickets return 404 |
| PATCH | `/api/tickets/{id}/status` | Agent | Change status. Resolving records the time |
| GET | `/api/inbox` | Signed in | Your messages (stands in for email in the demo) |
| POST | `/api/password-resets/{token}` | Link holder | Set a new password (12+ characters, not containing your username) |
| POST | `/api/automation/run-due` | Agent | Run due offboardings now instead of waiting for the worker |
| GET | `/api/approvals` | Manager or agent | Requests waiting for your decision |
| POST | `/api/approvals/{id}` | Approver | Approve, or reject with a reason |
| GET | `/api/audit` | Agent | Audit log, newest first. Filter with `target`, `action`, `ticket_id`, `limit` |

## Run the tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
```

Tests use in-memory SQLite and an in-memory directory by default, plus an ldap3 mock LDAP server. Coverage below 90% fails the run.

To also run the directory tests against the real OpenLDAP container, set `LDAP_TEST_URL` and `LDAP_TEST_BIND_PASSWORD`.

## Documentation

- [Business Requirements Document](docs/BRD.md)
- [User stories and acceptance criteria](docs/user-stories.md)
- [As-is and to-be process maps](docs/process-map.md)
- [SLA policy](docs/sla-policy.md)

## License

MIT
