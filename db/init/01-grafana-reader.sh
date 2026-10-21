#!/bin/sh
# Runs once, when the database volume is first created.
# Grafana gets its own login that can only read tables the app grants it
# (tickets and audit_events). It can't see users, tokens or reset links.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v reader_pw="$GRAFANA_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE grafana_reader LOGIN PASSWORD %L', :'reader_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader')
\gexec
GRANT USAGE ON SCHEMA public TO grafana_reader;
SQL
