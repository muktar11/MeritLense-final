"""Candidate interview links.

The full link carries the session id and its secret access token. The
short link (https://api.meritlense.com/s/<code>) is what employers share:
it redirects to the full link, so the assessment flow is unchanged. Its
random 8-character code is as hard to guess as needed for a link that
also expires with the session's own token.
"""
from urllib.parse import urlencode

from django.conf import settings


def interview_link(session, locale=None):
    base = (settings.FRONTEND_URL or "https://meritlense.com").rstrip("/")
    locale = (locale or session.ui_language or "en").lower()
    query = urlencode({"sessionId": str(session.public_id), "token": session.access_token})
    return f"{base}/{locale}/interview?{query}"


def interview_short_link(session, locale=None):
    from api.payments.short_links import short_url

    return short_url(interview_link(session, locale), purpose="interview_session", prefix="s")
