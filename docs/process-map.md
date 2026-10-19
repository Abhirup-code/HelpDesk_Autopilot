# Process Maps

GitHub renders these diagrams automatically.

## New hire: as-is (manual)

```mermaid
flowchart TD
    A[Manager emails IT about new hire] --> B{Email has all details?}
    B -- No --> C[IT emails back for missing info]
    C --> A
    B -- Yes --> D[IT creates account by hand]
    D --> E[IT guesses groups from department]
    E --> F[IT emails temporary password]
    F --> G[No record kept]
    G --> H[New hire often waits 1-2 days]
```

**Pain points:** back-and-forth emails, wrong or missing groups, passwords sent in email, no audit trail. Average time: 2 days.

## New hire: to-be (Autopilot)

```mermaid
flowchart TD
    A[Manager submits new-hire form] --> B[Form checks required fields]
    B --> C[Autopilot creates directory account]
    C --> D[Default groups added for department]
    D --> E[Audit record written]
    E --> F[Welcome message with reset link]
    F --> G[Ticket auto-resolved]
    C -. failure .-> X[Roll back and assign to IT]
```

**Result:** no back-and-forth, consistent groups, no passwords in email, every step logged. Target: under 10 minutes.

## Access request: to-be

```mermaid
flowchart TD
    A[Employee requests group access] --> B[Manager notified]
    B --> C{Manager decision}
    C -- Reject --> R[Ticket closed with reason]
    C -- Approve --> D{Privileged group?}
    D -- No --> E[Group added]
    D -- Yes --> F[IT approval required]
    F --> G{IT decision}
    G -- Approve --> E
    G -- Reject --> R
    E --> H[Audit record written]
    H --> I[Ticket resolved]
```

## Offboarding: as-is vs to-be

| Step | As-is | To-be |
|---|---|---|
| Trigger | HR remembers to email IT | Manager or HR files offboarding ticket with last day |
| Timing | Days or weeks late | Runs automatically at end of last day |
| Account | Sometimes left active | Disabled |
| Groups | Often left in place | All removed and listed in audit log |
| Evidence | None | Audit record per change |
