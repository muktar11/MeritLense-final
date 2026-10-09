"""Short links and "Pay now" buttons for payment and invoice emails.

Emails used to print the full Stripe URL. They now show a branded button
(api/core/emails.py) linking to a short link
(https://api.meritlense.com/p/<code>), also shown as text. The short link redirects to the original
URL, so nothing about how payment works changes.
"""
import secrets
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError

# Only these hosts can be shortened, so /p/<code> can never be used as an
# open redirect to an arbitrary site.
ALLOWED_TARGET_HOSTS = ("stripe.com", "meritlense.com")


def _host_allowed(url):
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    frontend_host = (urlparse(settings.FRONTEND_URL or "").hostname or "").lower()
    allowed = ALLOWED_TARGET_HOSTS + ((frontend_host,) if frontend_host else ())
    return any(host == h or host.endswith("." + h) for h in allowed)


def short_url(target_url, *, purpose=""):
    """Return a short link for target_url, reusing an existing one for the
    same target. Returns target_url unchanged when it can't be shortened
    (empty, not https, or not an allowed host)."""
    from .models import PaymentShortLink

    if not _host_allowed(target_url):
        return target_url
    base = (getattr(settings, "SHORT_LINK_BASE_URL", "") or "").rstrip("/")
    if not base:
        return target_url
    link = PaymentShortLink.objects.filter(target_url=target_url).order_by("id").first()
    while link is None:
        try:
            link = PaymentShortLink.objects.create(code=secrets.token_urlsafe(6), target_url=target_url,
                                                   purpose=purpose)
        except IntegrityError:  # code collision - try another
            link = None
    return f"{base}/p/{link.code}"
