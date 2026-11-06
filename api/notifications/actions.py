"""Where a notification's "Go to task" button leads.

Senders name the task's page when they send the email
(safe_send_mail(action=...)). The name travels on the ACTION_HEADER of the
message and is resolved per recipient when the in-app copy is saved,
because B2B and B2C accounts open the same task on different pages. An
action is either one of the PAGES keys below or a site path starting with
"/dashboard/" (e.g. a live-call page for one session).

Emails that name no action fall back to the first MeritLense dashboard link
in their text, so new emails with a dashboard link get a button without
any change. A login link ("sign in at ...") leads to the dashboard.

Paths are stored without the /en or /ar prefix: the frontend adds the
language the user is browsing in.
"""
import re
from urllib.parse import urlparse

from django.conf import settings

from api.core.constants import Roles

ACTION_HEADER = "X-MeritLense-Action"

_BUSINESS = (Roles.B2B, Roles.B2B_TEAM_MEMBER)
_ADMIN = (Roles.ADMIN, Roles.SUPERADMIN)


def _by_area(business, individual, admin=None):
    paths = {role: business for role in _BUSINESS}
    paths[Roles.B2C] = individual
    if admin:
        paths.update({role: admin for role in _ADMIN})
    return paths


PAGES = {
    "dashboard": _by_area("/dashboard/business", "/dashboard/indivisual", "/dashboard/admin"),
    "evaluations": _by_area("/dashboard/business/candidate-evaluation", "/dashboard/indivisual/evaluations"),
    "candidates": _by_area("/dashboard/business/candidates", "/dashboard/indivisual/candidates"),
    "results": _by_area("/dashboard/business/score-management", "/dashboard/indivisual/score-management"),
    "plans": _by_area("/dashboard/business/payment", "/dashboard/indivisual/payment"),
    "billing": _by_area("/dashboard/business/payment?tab=billing", "/dashboard/indivisual/payment?tab=billing"),
    "profile": _by_area("/dashboard/business/company-profile", "/dashboard/indivisual/profile"),
    # Employer accounts (B2B and B2C) are reviewed and approved here.
    "admin_accounts": {role: "/dashboard/admin/candidates" for role in _ADMIN},
    "admin_package_requests": {role: "/dashboard/admin/package-requests" for role in _ADMIN},
}

_SITE_PATH = re.compile(r"^/dashboard(/[A-Za-z0-9_\-/]*)?(\?[A-Za-z0-9_\-=&%.]*)?$")
_URL = re.compile(r"""https?://[^\s<>"'()\[\]{}]+""")
_LOCALE_PREFIX = re.compile(r"^/(en|ar)(?=/|$)")


def _site_hosts():
    host = (urlparse(getattr(settings, "FRONTEND_URL", "") or "").hostname or "").lower()
    hosts = {"meritlense.com", "www.meritlense.com"}
    if host:
        hosts |= {host, host.removeprefix("www."), "www." + host.removeprefix("www.")}
    return hosts


def resolve_action(action, user):
    """The site path for `action` as seen by `user`, or "" when there is
    none (unknown key, a page this kind of account doesn't have, or a path
    outside the dashboard)."""
    if not action:
        return ""
    action = str(action).strip()
    if action.startswith("/"):
        return action[:500] if _SITE_PATH.match(action) else ""
    return PAGES.get(action, {}).get(getattr(user, "role", None), "")


def action_from_body(body, user):
    """The first MeritLense dashboard (or login) link in an email's text,
    as a site path for `user`."""
    hosts = _site_hosts()
    for match in _URL.finditer(body or ""):
        url = urlparse(match.group(0).rstrip(".,;:!?"))
        if (url.hostname or "").lower() not in hosts:
            continue
        path = _LOCALE_PREFIX.sub("", url.path) or "/"
        if path.startswith("/auth/login"):
            return resolve_action("dashboard", user)
        if path.startswith("/dashboard"):
            found = resolve_action(path + (f"?{url.query}" if url.query else ""), user)
            if found:
                return found
    return ""
