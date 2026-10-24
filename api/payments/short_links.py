"""Short links and "Pay now" buttons for payment and invoice emails.

Emails used to print the full Stripe URL. They now show a button with an
icon, plus a short link (https://api.meritlense.com/p/<code>) for email
clients that don't render HTML. The short link redirects to the original
URL, so nothing about how payment works changes.
"""
import secrets
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError

# Only these hosts can be shortened, so /p/<code> can never be used as an
# open redirect to an arbitrary site. Meeting providers are included for the
# meeting links customers enter on live / in-person assessments.
MEETING_HOSTS = ("zoom.us", "zoom.com", "meet.google.com", "teams.microsoft.com", "teams.live.com",
                 "webex.com", "whereby.com", "meet.jit.si", "gotomeeting.com", "meet.goto.com")
ALLOWED_TARGET_HOSTS = ("stripe.com", "meritlense.com") + MEETING_HOSTS


def _host_allowed(url):
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    frontend_host = (urlparse(settings.FRONTEND_URL or "").hostname or "").lower()
    allowed = ALLOWED_TARGET_HOSTS + ((frontend_host,) if frontend_host else ())
    return any(host == h or host.endswith("." + h) for h in allowed)


def short_url(target_url, *, purpose="", prefix="p"):
    """Return a short link for target_url, reusing an existing one for the
    same target. Returns target_url unchanged when it can't be shortened
    (empty, not https, or not an allowed host). prefix is the URL path
    segment: "p" for payment/invoice links, "s" for interview sessions."""
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
    return f"{base}/{prefix}/{link.code}"


def meeting_short_link(meeting_link):
    """Short link for an assessment's meeting link (our AI interview page or
    the customer's Zoom/Meet/Teams link), or None when it can't be
    shortened (another site, or empty)."""
    if not meeting_link:
        return None
    short = short_url(meeting_link, purpose="meeting", prefix="s")
    return short if short != meeting_link else None


def email_button_html(*, greeting, paragraphs, button_label, button_url, icon=None, footer_lines=(), language="en",
                      title=None):
    """An email with one call-to-action button, in the shared MeritLense
    layout (api/core/email_template.py). "A: 1 · B: 2" paragraphs become a
    details table; footer_lines follow the button, and any sign-off in them
    is dropped because the layout adds the approved signature. The title,
    when not given, is the email's subject. icon is no longer shown."""
    from api.core.email_template import blocks_from_paragraphs, render_email, without_sign_off

    blocks = blocks_from_paragraphs(paragraphs, language)
    blocks.append(("button", (button_label, button_url)))
    blocks.extend(("p", line) for line in without_sign_off(footer_lines))
    return render_email(language=language, title=title, greeting=greeting, blocks=blocks)
