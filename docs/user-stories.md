# User Stories

Priorities use MoSCoW (Must, Should, Could, Won't). Each story maps to requirements in the [BRD](BRD.md).

## Epic 1: Ticket portal

### US-1: Submit a ticket (Must, FR-1)
**As an** employee, **I want** to submit an IT request in one form, **so that** I don't have to email or chase IT.

**Acceptance criteria**
- **Given** I am signed in, **when** I choose a type, enter a description and submit, **then** a ticket is created with status `New` and I see its ticket ID.
- **Given** the description is empty, **when** I submit, **then** I see an error and no ticket is created.

### US-2: Track my tickets (Must, FR-2)
**As an** employee, **I want** to see the status of my tickets, **so that** I know when my issue will be fixed.

**Acceptance criteria**
- **Given** I have tickets, **when** I open My Tickets, **then** I see only my own tickets with status and due time.
- **Given** another user's ticket ID, **when** I try to open it, **then** I get a 404.

## Epic 2: Automation

### US-3: Reset my own password (Must, FR-3)
**As an** employee locked out of my account, **I want** a reset link right away, **so that** I can get back to work without waiting for IT.

**Acceptance criteria**
- **Given** a password reset ticket, **when** it is created, **then** a single-use reset link is issued and the ticket is marked `Auto-resolved`.
- **Given** a link older than 15 minutes, **when** I use it, **then** it is rejected.
- **Given** a link that was already used, **when** I use it again, **then** it is rejected.

### US-4: New hire ready on day one (Must, FR-4)
**As a** manager, **I want** my new hire's account created from one request, **so that** they can work on their first day.

**Acceptance criteria**
- **Given** a new-hire ticket with name, department and start date, **when** it is processed, **then** a directory account exists with the department's default groups.
- **Given** the username already exists, **when** it is processed, **then** a unique username is generated (for example `jsmith2`).
- **Given** any step fails, **then** no partial account remains and the ticket goes to IT.

### US-5: Remove access on the last day (Must, FR-5)
**As a** security lead, **I want** departing employees disabled on schedule, **so that** former staff cannot sign in.

**Acceptance criteria**
- **Given** an offboarding ticket with a last day, **when** that date ends, **then** the account is disabled and removed from all groups.
- **Given** the account is disabled, **then** an audit record lists every group removed.

## Epic 3: Approvals and audit

### US-6: Approve team access (Must, FR-6)
**As a** manager, **I want** to approve or reject access requests for my team, **so that** people only get access they need.

**Acceptance criteria**
- **Given** an access request, **then** its status is `Waiting approval` and only the requester's manager can approve it.
- **Given** a manager approves, **then** the group is added and the ticket is resolved.
- **Given** a manager rejects, **then** no change is made and the reason is shown to the requester.

### US-7: Extra check for admin access (Must, FR-7)
**As an** IT manager, **I want** privileged groups to need my sign-off too, **so that** admin rights are never granted automatically.

**Acceptance criteria**
- **Given** a request for a privileged group, **when** the manager approves, **then** status becomes `Waiting IT approval`, not resolved.

### US-8: Audit every change (Must, FR-8)
**As a** compliance reviewer, **I want** a record of every account change, **so that** I can prove access is controlled.

**Acceptance criteria**
- **Given** any directory change, **then** an audit record holds the actor, action, target user, ticket ID and timestamp.
- **Given** an audit record, **when** anyone tries to edit or delete it through the API, **then** the request is refused.

## Epic 4: SLA and reporting

### US-9: Know when tickets are late (Must, FR-9, FR-10)
**As an** IT agent, **I want** each ticket to show its due time, **so that** I work on the most urgent ones first.

**Acceptance criteria**
- **Given** a new ticket, **then** its due time follows the [SLA policy](sla-policy.md).
- **Given** a ticket past its due time and not resolved, **then** it is marked `Breached`.

### US-10: SLA dashboard (Should, FR-13)
**As an** IT manager, **I want** a live dashboard, **so that** I can report on team performance.

**Acceptance criteria**
- The dashboard shows open tickets by type, median resolution time, breach count and auto-resolve rate.
- Data refreshes at least every 30 seconds.

### US-11: Bulk onboarding (Should, FR-12)
**As an** IT agent, **I want** to upload a CSV of new hires, **so that** a whole intern class is set up in one step.

**Acceptance criteria**
- **Given** a valid CSV, **then** one account is created per row and a summary shows created and failed rows.
- **Given** a row with a missing department, **then** that row fails with a clear message and the others still succeed.
