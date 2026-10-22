#!/usr/bin/env bash
# End-to-end check of the full Docker Compose stack: directory, database, API,
# worker, Prometheus and Grafana. Used by CI; also runs on a laptop with Docker.
set -euo pipefail

API=http://127.0.0.1:8000
step() { printf '\n== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
# Save output first, then search it. Piping straight into "grep -q" can fail
# under pipefail: grep stops reading early and the writer gets a broken pipe.
contains() { grep -qF -- "$1" <<<"$2"; }

[ -f .env ] || cp .env.example .env

step "Start the stack"
docker compose up --build -d --wait --wait-timeout 240

step "Seed demo users"
tokens=$(docker compose exec -T api python -m app.seed)
token_for() { awk -v u="$1" '$1 == u { print $2 }' <<<"$tokens"; }
EMMA=$(token_for emma); LIAM=$(token_for liam); AVA=$(token_for ava)
[ -n "$EMMA" ] && [ -n "$LIAM" ] && [ -n "$AVA" ] || fail "seed did not print tokens"

post() { curl -fsS -X POST "$API$1" -H "Authorization: Bearer $2" -H "Content-Type: application/json" -d "$3"; }
field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

step "New user sets a password with the setup link, then signs in"
link_for() { awk -v u="$1" '$1 == u { print $3 }' <<<"$tokens"; }
# First csrf token on a page (sed stops at the first match, so no broken pipe).
csrf_from() { sed -n '/name="csrf"/{s/.*name="csrf" value="\([^"]*\)".*/\1/p;q;}' <<<"$1"; }
link=$(link_for emma)
[ -n "$link" ] || fail "seed did not print a setup link"
jar=$(mktemp)
chosen="e2e-chosen-password-1"
page=$(curl -fsS -b "$jar" -c "$jar" "$link")
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$jar" -c "$jar" -X POST "$link" \
    --data-urlencode "password=$chosen" --data-urlencode "confirm=$chosen" \
    --data-urlencode "csrf=$(csrf_from "$page")")
[ "$code" = 303 ] || fail "setting a password with the setup link returned $code"
page=$(curl -fsS -b "$jar" -c "$jar" "$API/login")
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$jar" -c "$jar" -X POST "$API/login" \
    --data-urlencode "username=emma" --data-urlencode "password=$chosen" \
    --data-urlencode "csrf=$(csrf_from "$page")" --data-urlencode "next=/tickets")
[ "$code" = 303 ] || fail "portal sign-in returned $code"
tickets_page=$(curl -fsS -b "$jar" "$API/tickets")
contains "My tickets" "$tickets_page" || fail "tickets page not shown after sign-in"
rm -f "$jar"

step "Password reset resolves itself"
status=$(post /api/tickets "$EMMA" '{"type":"password_reset","title":"Locked out","description":"MFA"}' | field '["status"]')
[ "$status" = auto_resolved ] || fail "password reset status was $status"

step "New hire creates a directory account"
body='{"type":"new_hire","title":"New analyst","description":"e2e","details":{"first_name":"Jane","last_name":"Doe","department":"Finance","start_date":"2030-01-07"}}'
username=$(post /api/tickets "$LIAM" "$body" | field '["details"]["username"]')
found=$(docker compose exec -T ldap ldapsearch -x -LLL -H ldap://127.0.0.1:1389 \
    -D cn=admin,dc=example,dc=com -w "$(grep ^LDAP_ADMIN_PASSWORD= .env | cut -d= -f2)" \
    -b ou=Users,dc=example,dc=com "(uid=$username)" dn 2>&1 || true)
contains "uid=$username,ou=Users" "$found" \
    || fail "new hire $username not found in the directory. Search returned: $found"

step "Admin rights need manager and IT approval"
id=$(post /api/tickets "$EMMA" '{"type":"access_request","title":"Admin","description":"e2e","details":{"group":"it-admins"}}' | field '["id"]')
[ "$(post "/api/approvals/$id" "$LIAM" '{"decision":"approve"}' | field '["status"]')" = waiting_it_approval ] \
    || fail "manager approval should wait for IT"
[ "$(post "/api/approvals/$id" "$AVA" '{"decision":"approve"}' | field '["status"]')" = resolved ] \
    || fail "IT approval should resolve the request"

step "Nobody approves their own request"
own=$(post /api/tickets "$AVA" '{"type":"access_request","title":"VPN","description":"e2e","details":{"group":"vpn-users"}}' | field '["id"]')
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/approvals/$own" \
    -H "Authorization: Bearer $AVA" -H "Content-Type: application/json" -d '{"decision":"approve"}')
[ "$code" = 403 ] || fail "self-approval returned $code"

step "Audit log refuses deletes"
if docker compose exec -T db psql -U helpdesk -c "delete from audit_events;" 2>/dev/null; then
    fail "audit log rows were deleted"
fi

step "Demo data and metrics"
docker compose exec -T api python -m app.demo --tickets 100
metrics=$(curl -fsS "$API/metrics")
contains "helpdesk_auto_resolve_ratio" "$metrics" || fail "metrics missing"

step "Prometheus scrapes the API"
targets=""
for _ in $(seq 1 30); do
    targets=$(curl -fsS http://127.0.0.1:9090/api/v1/targets || true)
    contains '"health":"up"' "$targets" && break
    sleep 2
done
contains '"health":"up"' "$targets" || fail "API target not up: $targets"

step "Grafana is healthy and has the dashboard"
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:3000/api/health >/dev/null && break; sleep 2; done
# The password is read from .env at runtime; nothing secret is stored here.
grafana() { curl -fsS -u "admin:$(grep ^GRAFANA_ADMIN_PASSWORD= .env | cut -d= -f2)" "http://127.0.0.1:3000$1"; } # gitleaks:allow
dashboard=$(grafana /api/dashboards/uid/helpdesk-sla)
contains '"title":"HelpDesk Autopilot: SLA"' "$dashboard" || fail "dashboard not provisioned"
db_health=$(grafana /api/datasources/uid/helpdesk-db/health)
contains '"status":"OK"' "$db_health" || fail "Grafana cannot reach PostgreSQL: $db_health"

echo
echo "All end-to-end checks passed."
