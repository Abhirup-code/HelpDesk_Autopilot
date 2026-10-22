#!/usr/bin/env bash
# End-to-end check of the full Docker Compose stack: directory, database, API,
# worker, Prometheus and Grafana. Used by CI; also runs on a laptop with Docker.
set -euo pipefail

API=http://127.0.0.1:8000
step() { printf '\n== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

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

step "Password reset resolves itself"
status=$(post /api/tickets "$EMMA" '{"type":"password_reset","title":"Locked out","description":"MFA"}' | field '["status"]')
[ "$status" = auto_resolved ] || fail "password reset status was $status"

step "New hire creates a directory account"
body='{"type":"new_hire","title":"New analyst","description":"e2e","details":{"first_name":"Jane","last_name":"Doe","department":"Finance","start_date":"2030-01-07"}}'
username=$(post /api/tickets "$LIAM" "$body" | field '["details"]["username"]')
docker compose exec -T ldap ldapsearch -x -LLL -H ldap://127.0.0.1:1389 \
    -D cn=admin,dc=example,dc=com -w "$(grep ^LDAP_ADMIN_PASSWORD= .env | cut -d= -f2)" \
    -b ou=Users,dc=example,dc=com "(uid=$username)" dn | grep -q "uid=$username" \
    || fail "new hire $username not found in the directory"

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
curl -fsS "$API/metrics" | grep -q '^helpdesk_auto_resolve_ratio' || fail "metrics missing"

step "Prometheus scrapes the API"
for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:9090/api/v1/targets | grep -q '"health":"up"' && break
    sleep 2
done
curl -fsS http://127.0.0.1:9090/api/v1/targets | grep -q '"health":"up"' || fail "API target not up"

step "Grafana is healthy and has the dashboard"
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:3000/api/health >/dev/null && break; sleep 2; done
# The password is read from .env at runtime; nothing secret is stored here.
grafana() { curl -fsS -u "admin:$(grep ^GRAFANA_ADMIN_PASSWORD= .env | cut -d= -f2)" "http://127.0.0.1:3000$1"; } # gitleaks:allow
grafana /api/dashboards/uid/helpdesk-sla | grep -q '"title":"HelpDesk Autopilot: SLA"' \
    || fail "dashboard not provisioned"
grafana /api/datasources/uid/helpdesk-db/health | grep -q '"status":"OK"' \
    || fail "Grafana cannot reach PostgreSQL"

echo
echo "All end-to-end checks passed."
