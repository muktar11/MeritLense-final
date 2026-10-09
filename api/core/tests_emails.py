"""Branded emails and interview calendar invites (api/core/emails.py)."""
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from uuid import uuid4

from django.core import mail
from django.test import SimpleTestCase, override_settings

from api.core.emails import Email, email_from_plain_text, render_html, render_text
from api.evaluations.utils import (
    send_evaluation_cancelled_email,
    send_evaluation_rescheduled_email,
    send_evaluation_scheduled_email,
)

START = datetime(2026, 10, 13, 8, 0, tzinfo=dt_timezone.utc)


def _evaluation(**overrides):
    user = SimpleNamespace(email="owner@example.com", first_name="Omar", get_full_name=lambda: "Omar Owner")
    values = dict(
        public_id=uuid4(), candidate_first_name="Asmaa", candidate_last_name="Ali",
        candidate_email="asmaa@example.com", candidate_passport_id="P123", candidate_job_role="",
        scheduled_date=START, duration_minutes=45, meeting_link="https://meritlense.com/en/interview?token=abc",
        meeting_id="", meeting_password="", location="", cancellation_reason="Position filled",
        company_id=1, company=SimpleNamespace(name="Gulf Hospitality", admin_user_id=1, admin_user=user),
        created_by=user, session=SimpleNamespace(role_name="Front Desk Agent"),
        get_evaluation_type_display=lambda: "AI Interview", completed_at=None, certificate_status="", certificate_url="",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _calendar_part(message):
    return next(content for content, mimetype in message.alternatives if mimetype.startswith("text/calendar"))


@override_settings(DEFAULT_FROM_EMAIL="MeritLense <no-reply@meritlense.com>",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InterviewInviteTests(SimpleTestCase):
    def test_invitation_is_a_designed_email_with_a_calendar_invite(self):
        send_evaluation_scheduled_email(_evaluation())

        candidate, owner = mail.outbox
        self.assertEqual(candidate.to, ["asmaa@example.com"])
        html = next(c for c, m in candidate.alternatives if m == "text/html")
        self.assertIn("You&#x27;re invited to an interview", html)
        self.assertIn("Join your interview", html)
        self.assertIn("https://meritlense.com/en/interview?token=abc", candidate.body)  # plain text keeps the link

        ics = _calendar_part(candidate)
        self.assertIn("METHOD:REQUEST", ics)
        self.assertIn("DTSTART:20261013T080000Z", ics)
        self.assertIn("DTEND:20261013T084500Z", ics)
        self.assertIn("ATTENDEE;CN=Asmaa Ali", ics)
        self.assertIn("ORGANIZER;CN=MeritLense:mailto:no-reply@meritlense.com", ics)
        self.assertTrue(any(a[0] == "invite.ics" for a in candidate.attachments))
        self.assertEqual(owner.to, ["owner@example.com"])
        self.assertIn("METHOD:REQUEST", _calendar_part(owner))

    def test_reschedule_updates_and_cancel_removes_the_same_calendar_event(self):
        evaluation = _evaluation()
        send_evaluation_scheduled_email(evaluation)
        uid = [line for line in _calendar_part(mail.outbox[0]).splitlines() if line.startswith("UID:")][0]

        evaluation.scheduled_date = START + timedelta(days=1)
        send_evaluation_rescheduled_email(evaluation, old_date=START)
        resched = _calendar_part(mail.outbox[-1])
        self.assertIn(uid, resched)
        self.assertIn("METHOD:REQUEST", resched)
        self.assertIn("DTSTART:20261014T080000Z", resched)

        send_evaluation_cancelled_email(evaluation)
        cancel = _calendar_part(mail.outbox[-1])
        self.assertIn(uid, cancel)
        self.assertIn("METHOD:CANCEL", cancel)
        self.assertIn("STATUS:CANCELLED", cancel)
        self.assertIn("Position filled", mail.outbox[-1].body)


class BrandedLayoutTests(SimpleTestCase):
    def test_plain_text_emails_are_wrapped_in_the_branded_layout(self):
        email = email_from_plain_text("Welcome", """
        Hello Sara,

        Your account is ready.

        Login Email: sara@example.com

        To sign in, go to:
        https://meritlense.com/en/auth/login

        Best regards,
        Meritlense Team
        """)
        self.assertEqual(email.greeting, "Hello Sara,")
        self.assertEqual(email.intro, ["Your account is ready."])
        self.assertIn(("Login Email", "sara@example.com"), email.details)
        self.assertEqual(email.button, ("Sign in", "https://meritlense.com/en/auth/login"))
        html = render_html(email)
        self.assertIn("meritlense.com/logo.png", html)
        self.assertEqual(html.count("Best regards"), 1)

    def test_text_version_has_the_same_content(self):
        email = Email(subject="Code", title="Verify your email", code="48213", code_label="Your code",
                      details=[("Account", "a@b.com"), ("Empty", "")])
        text = render_text(email)
        self.assertIn("Your code: 48213", text)
        self.assertIn("Account: a@b.com", text)
        self.assertNotIn("Empty", text)
