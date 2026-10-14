"""Every outgoing email shows short links instead of long URLs.

EMAIL_BACKEND is ShortLinkEmailBackend, which rewrites each message just
before handing it to the real backend (EMAIL_INNER_BACKEND, SMTP in
production). Doing it here covers every email - safe_send_mail, direct
send_mail calls, and any email added later - without each sender having to
remember.

What is shortened: https links to MeritLense or Stripe (the hosts
short_url() allows, so the redirect can't be abused for other sites) that
are longer than the short link itself, in the plain-text body and in HTML
links and link text. Image sources are left alone, and so are links that
are already short. Shortening never blocks an email: on any error the
message goes out unchanged.
"""
import html
import logging
import re

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailAlternative
from django.db import transaction

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"""https://[^\s<>"'()\[\]{}]+""")
TRAILING_PUNCTUATION = ".,;:!?"


def _short_base():
    return (getattr(settings, "SHORT_LINK_BASE_URL", "") or "").rstrip("/")


def _shorten(url, cache):
    """Short link for url, or url itself when it shouldn't be shortened."""
    from api.payments.short_links import short_url

    if url in cache:
        return cache[url]
    base = _short_base()
    result = url
    # Not when already short, or when the short link wouldn't be shorter.
    if base and not url.startswith(base + "/") and len(url) > len(base) + 12:
        result = short_url(url, purpose="email", prefix="l")
    cache[url] = result
    return result


def _split_trailing(url):
    trail = ""
    while url and url[-1] in TRAILING_PUNCTUATION:
        trail = url[-1] + trail
        url = url[:-1]
    return url, trail


def shorten_text(text, cache=None):
    cache = {} if cache is None else cache

    def replace(match):
        url, trail = _split_trailing(match.group(0))
        return _shorten(url, cache) + trail

    return URL_PATTERN.sub(replace, text or "")


def shorten_html(content, cache=None):
    cache = {} if cache is None else cache

    def replace(match):
        before = content[max(0, match.start() - 12):match.start()].lower()
        if re.search(r"""(src|background)\s*=\s*["']?$|url\(\s*["']?$""", before):
            return match.group(0)  # an image or style URL, not a link
        raw, trail = _split_trailing(match.group(0))
        short = _shorten(html.unescape(raw), cache)
        return (html.escape(short, quote=True) if short != html.unescape(raw) else raw) + trail

    return URL_PATTERN.sub(replace, content or "")


def shorten_message_links(message):
    cache = {}
    if getattr(message, "content_subtype", "plain") == "html":
        message.body = shorten_html(message.body, cache)
    else:
        message.body = shorten_text(message.body, cache)
    alternatives = getattr(message, "alternatives", None)
    if alternatives:
        message.alternatives = [
            EmailAlternative(shorten_html(alt[0], cache), alt[1])
            if isinstance(alt[0], str) and (alt[1] or "").startswith("text/html") else alt
            for alt in alternatives
        ]
    return message


class ShortLinkEmailBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.inner = get_connection(settings.EMAIL_INNER_BACKEND, fail_silently=fail_silently, **kwargs)

    def open(self):
        return self.inner.open()

    def close(self):
        return self.inner.close()

    def send_messages(self, email_messages):
        for message in email_messages or []:
            try:
                # A savepoint: emails are often sent inside the caller's
                # transaction, which a failed short-link write must not break.
                with transaction.atomic():
                    shorten_message_links(message)
            except Exception:
                logger.exception("Could not shorten links in email %r; sending it unchanged",
                                 getattr(message, "subject", ""))
        return self.inner.send_messages(email_messages)
