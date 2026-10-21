"""Business rules that IT would want to change without touching code paths."""

from datetime import timedelta

# Groups every new hire in a department gets on day one.
DEPARTMENT_GROUPS: dict[str, list[str]] = {
    "Engineering": ["all-staff", "git-users", "vpn-users"],
    "Finance": ["all-staff", "finance-share", "erp-users"],
    "IT": ["all-staff", "helpdesk-agents"],
    "Operations": ["all-staff", "erp-users", "tableau-users"],
    "Sales": ["all-staff", "tableau-users"],
}
DEPARTMENTS = sorted(DEPARTMENT_GROUPS)

# Groups people can ask for with an access request.
REQUESTABLE_GROUPS = [
    "erp-users",
    "finance-share",
    "git-users",
    "helpdesk-agents",
    "it-admins",
    "tableau-users",
    "vpn-users",
]

# Groups that also need an IT agent's approval after the manager approves.
PRIVILEGED_GROUPS = {"it-admins"}

# How long password links stay valid.
RESET_LINK_TTL = timedelta(minutes=15)
NEW_HIRE_LINK_TTL = timedelta(hours=72)

MIN_PASSWORD_LENGTH = 12
