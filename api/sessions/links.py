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


INVITATION_TEXT = {
    "en": {
        "subject": "Your MeritLense assessment link",
        "body": (
            "Hello {name},\n\n"
            "{company} has invited you to a MeritLense workforce readiness assessment for the role of {role}.\n\n"
            "Start your assessment here:\n{link}\n\n"
            "Before you begin:\n"
            "- Use a device with a camera and microphone, in a quiet place.\n"
            "- Have your identification document ready.\n"
            "- The link is personal to you; please do not share it.\n\n"
            "Best regards,\nMeritLense Team"
        ),
    },
    "ar": {
        "subject": "رابط تقييم MeritLense الخاص بك",
        "body": (
            "مرحبًا {name}،\n\n"
            "دعتك {company} لإجراء تقييم جاهزية القوى العاملة من MeritLense لوظيفة {role}.\n\n"
            "ابدأ التقييم من هنا:\n{link}\n\n"
            "قبل البدء:\n"
            "- استخدم جهازًا مزودًا بكاميرا وميكروفون، في مكان هادئ.\n"
            "- جهّز وثيقة الهوية الخاصة بك.\n"
            "- هذا الرابط خاص بك؛ يرجى عدم مشاركته.\n\n"
            "مع أطيب التحيات،\nفريق MeritLense"
        ),
    },
}


def send_interview_link_email(session, locale=None):
    """Email the candidate their short interview link, in the interview's
    UI language. Returns the address it went to; raises if the mail server
    refuses or email isn't configured, so the caller can tell the employer."""
    from api.accounts.utils import safe_send_mail

    candidate = session.candidate
    locale = (locale or session.ui_language or "en").lower()
    text = INVITATION_TEXT["ar" if locale.startswith("ar") else "en"]
    creator = session.created_by
    company = (
        session.organization.name if session.organization_id
        else (creator.get_full_name() if creator else "") or "MeritLense"
    )
    body = text["body"].format(
        name=f"{candidate.first_name} {candidate.last_name}".strip(),
        company=company,
        role=session.role_name,
        link=interview_short_link(session, locale),
    )
    if not safe_send_mail(text["subject"], body, [candidate.email]):
        raise RuntimeError("Email is not configured on this server.")
    return candidate.email
