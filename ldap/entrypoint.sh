#!/bin/sh
# Starts the demo OpenLDAP server. On first start it writes the config and
# loads the base tree, groups and service account.
set -eu

: "${LDAP_ADMIN_PASSWORD:?set LDAP_ADMIN_PASSWORD}"
: "${LDAP_SERVICE_PASSWORD:?set LDAP_SERVICE_PASSWORD}"

SCHEMADIR="${SCHEMADIR:-/etc/openldap/schema}"
MODULEDIR="${MODULEDIR:-/usr/lib/openldap}"
DATADIR="${DATADIR:-/var/lib/openldap/data}"
RUNDIR="${RUNDIR:-/run/openldap}"
CONF="${CONF:-$DATADIR/../slapd.conf}"
PORT="${LDAP_PORT:-1389}"
SLAPD="${SLAPD:-slapd}"

mkdir -p "$DATADIR" "$RUNDIR"

if [ ! -f "$DATADIR/data.mdb" ]; then
    echo "First start: writing config and loading base tree"
    hash() {
        "$SLAPD" -T passwd -o module-path="$MODULEDIR" -o module-load=argon2 -h '{ARGON2}' -s "$1"
    }
    root_hash=$(hash "$LDAP_ADMIN_PASSWORD")
    service_hash=$(hash "$LDAP_SERVICE_PASSWORD")
    sed -e "s|__SCHEMADIR__|$SCHEMADIR|g" -e "s|__MODULEDIR__|$MODULEDIR|g" \
        -e "s|__DATADIR__|$DATADIR|g" -e "s|__RUNDIR__|$RUNDIR|g" \
        -e "s|__ROOTPW__|$root_hash|g" \
        /etc/helpdesk-ldap/slapd.conf.template > "$CONF"
    sed -e "s|__SERVICE_PW__|$service_hash|g" /etc/helpdesk-ldap/bootstrap.ldif.template \
        | "$SLAPD" -T add -f "$CONF"
fi

exec "$SLAPD" -d 256 -h "ldap://0.0.0.0:$PORT/" -f "$CONF"
