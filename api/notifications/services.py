import logging
import textwrap

from django.db import transaction
from django.db.models.functions import Lower

logger = logging.getLogger(__name__)

# Set on an email (safe_send_mail(in_app=False)) to keep it out of the
# in-app feed: sign-in/signing codes, password resets, verification links
# and credentials must only reach the mailbox, or anyone holding a logged-in
# session could read them. Stripped before the email is sent.
IN_APP_HEADER = "X-MeritLense-In-App"


class NotificationService:
    @classmethod
    def notify_interview_session_created(cls, session):
        return {
            "session_id": str(session.public_id),
            "status": session.status,
        }


def _plain_body(message):
    body = message.body or ""
    if getattr(message, "content_subtype", "plain") == "html":
        from django.utils.html import strip_tags
        body = strip_tags(body)
    return textwrap.dedent(body).strip()


def record_email_notifications(message):
    """Saves an in-app Notification for each registered, active recipient of
    `message`. Called from the email backend with the message as the sender
    wrote it (before branding), so the feed shows the plain text. Never
    blocks the email: any error is logged and swallowed."""
    from api.accounts.models import User
    from .models import Notification

    from .actions import ACTION_HEADER, action_from_body, resolve_action

    headers = getattr(message, "extra_headers", None) or {}
    opt_out = str(headers.pop(IN_APP_HEADER, "")).lower() == "no"
    action = headers.pop(ACTION_HEADER, "")
    if opt_out:
        return 0

    recipients = {e.strip().lower() for e in (message.to or []) if e and e.strip()}
    if not recipients:
        return 0
    try:
        # A savepoint: a failed write must not break the caller's transaction.
        with transaction.atomic():
            users = list(
                User.objects.annotate(email_lower=Lower("email"))
                .filter(email_lower__in=recipients, is_active=True)
            )
            title = (message.subject or "").strip()[:255]
            body = _plain_body(message)
            Notification.objects.bulk_create([
                Notification(
                    user=user, title=title, body=body,
                    action_url=resolve_action(action, user) or action_from_body(body, user),
                )
                for user in users
            ])
            return len(users)
    except Exception:
        logger.exception("Could not record in-app notification for email %r", getattr(message, "subject", ""))
        return 0
