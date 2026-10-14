"""Where a request came from, for the audit trail.

nginx is the only proxy in front of gunicorn (api.meritlense.com resolves
straight to the VM) and appends the address it saw to X-Forwarded-For.
Everything to the left of that entry is whatever the client sent, so it can
be forged: reading the first entry let any client choose the IP recorded
against its activity. The client IP is therefore read from the right,
AUDIT_TRUSTED_PROXY_COUNT entries from the end.

The middleware keeps the current request available to AuditLogService, so
an entry written anywhere during a request (services, signals, system
events triggered by a candidate's interview link) records its origin
without every caller having to pass the request along.
"""
import contextvars
import ipaddress

from django.conf import settings

_current_request = contextvars.ContextVar("audit_current_request", default=None)


def current_request():
    return _current_request.get()


def _valid_ip(value):
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def get_client_ip(request):
    if request is None:
        return None
    meta = getattr(request, "META", None) or {}
    trusted = max(int(getattr(settings, "AUDIT_TRUSTED_PROXY_COUNT", 1)), 0)
    forwarded = [part.strip() for part in (meta.get("HTTP_X_FORWARDED_FOR") or "").split(",") if part.strip()]
    if trusted and forwarded:
        # Fewer entries than trusted proxies: the leftmost was added by the
        # first proxy, so it is still the client.
        return _valid_ip(forwarded[-trusted] if len(forwarded) >= trusted else forwarded[0])
    return _valid_ip(meta.get("REMOTE_ADDR"))


def request_audit_fields(request=None):
    """ip_address / user_agent / request_method / request_path for an audit
    entry: from the given request, else the one being served, else blank
    (management commands, background work)."""
    request = request if request is not None else current_request()
    if request is None:
        return {}
    meta = getattr(request, "META", None) or {}
    return {
        "ip_address": get_client_ip(request),
        "user_agent": meta.get("HTTP_USER_AGENT", ""),
        "request_method": (getattr(request, "method", "") or "")[:10],
        "request_path": (getattr(request, "path", "") or "")[:500],
    }


class AuditRequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            _current_request.reset(token)
