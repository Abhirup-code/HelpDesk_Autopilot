FROM alpine:3.22
RUN apk upgrade --no-cache && apk add --no-cache openldap openldap-back-mdb openldap-passwd-argon2 openldap-clients \
    && mkdir -p /var/lib/openldap/data /run/openldap /etc/helpdesk-ldap \
    && chown -R ldap:ldap /var/lib/openldap /run/openldap
COPY slapd.conf.template bootstrap.ldif.template /etc/helpdesk-ldap/
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh
USER ldap
EXPOSE 1389
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
    CMD ldapsearch -x -H ldap://127.0.0.1:1389 -b "" -s base >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
