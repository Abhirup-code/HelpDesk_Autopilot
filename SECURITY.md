# Security Policy

## Reporting a vulnerability

Please don't open a public issue. Use **Report a vulnerability** under this repository's
Security tab (private vulnerability reporting).

## How this project is protected

| Layer | Control |
|---|---|
| Code | CodeQL (security-extended) and ruff security rules on every pull request |
| Secrets | gitleaks scans the full git history; secrets only come from `.env`, which is git-ignored |
| Dependencies | Pinned versions, pip-audit on every run and weekly, Dependabot updates, dependency review on PRs |
| Containers | Trivy scans both images and Dockerfiles; containers run as non-root users |
| Pipeline | Actions pinned to commit SHAs, read-only token, zizmor workflow lint, protected `main` branch |
| Directory | The app uses a service account that can only manage users and groups |
| Passwords | Hashed by the directory with Argon2id; reset links are single-use, expire in 15 minutes, stored as SHA-256 hashes |
| Access | Manager approval for access, IT approval for admin rights, no self-approval |
| Audit | Every change is logged; database triggers block edits and deletes; denied attempts are logged too |
| Dashboard | Grafana reads with its own login that can only see tickets and the audit log |
| Web portal | Directory sign-in, signed session cookies, CSRF tokens on every form, lockout after 5 wrong passwords, same-site redirects only, strict Content-Security-Policy |

## Demo credentials

`.env.example` contains placeholder passwords for local use only. Change all of them
before running this anywhere other than your own machine.
