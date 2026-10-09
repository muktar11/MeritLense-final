"""Short links and "Pay now" buttons for payment and invoice emails.

Emails used to print the full Stripe URL. They now show a button with an
icon, plus a short link (https://api.meritlense.com/p/<code>) for email
clients that don't render HTML. The short link redirects to the original
URL, so nothing about how payment works changes.
"""
import secrets
from html import escape
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


def email_button_html(*, greeting, paragraphs, button_label, button_url, icon="💳", footer_lines=()):
    """A simple HTML email body with one call-to-action button. Table-based
    and inline-styled so it renders in Outlook, Gmail and mobile clients."""
    body = "".join(
        f'<p style="margin:0 0 14px;font-size:15px;line-height:1.5;color:#1f2a33;">{escape(p)}</p>'
        for p in paragraphs if p
    )
    footer = "<br>".join(escape(line) for line in footer_lines)
    url = escape(button_url, quote=True)
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f4f6f8;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:8px;padding:28px 28px 24px;font-family:Arial,Helvetica,sans-serif;">
<tr><td>
<p style="margin:0 0 16px;font-size:15px;color:#1f2a33;">{escape(greeting)}</p>
{body}
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 20px;">
<tr><td style="border-radius:6px;background:#1f5d73;">
<a href="{url}" target="_blank" style="display:inline-block;padding:12px 26px;font-size:16px;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:6px;">{icon}&nbsp; {escape(button_label)}</a>
</td></tr>
</table>
<p style="margin:0 0 18px;font-size:12px;color:#5a6670;">If the button doesn't work, open this link: <a href="{url}" style="color:#1f5d73;">{escape(button_url)}</a></p>
<p style="margin:0;font-size:14px;line-height:1.5;color:#1f2a33;">{footer}</p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""
