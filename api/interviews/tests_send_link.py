"""POST interviews/{id}/send-link: MeritLense emails the candidate their
interview link (the old "share by email" button was a mailto: link that did
nothing without a configured mail app)."""
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api.accounts.models import User
from api.candidates.models import Candidate
from api.core.constants import InterviewEvaluationTier, InterviewSessionStatus, Roles
from api.interviews.models import InterviewConfiguration
from api.sessions.models import InterviewSession


@override_settings(EMAIL_HOST="smtp.test", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SendInterviewLinkTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@example.com", password="x", first_name="Olive",
                                              last_name="Owner", role=Roles.B2C, is_verified=True)
        self.other = User.objects.create_user(email="other@example.com", password="x", first_name="O",
                                              last_name="T", role=Roles.B2C, is_verified=True)
        self.candidate = Candidate.objects.create(
            first_name="Sara", last_name="Ali", email="sara@example.com", passport_id="P-1", job_role="NA",
            core_skills="care", preferred_language="EN", passport_document="candidates/documents/passport/t.pdf",
            created_by=self.owner)
        config = InterviewConfiguration.objects.create(
            role_name="Child Caregiver", role_code="child_caregiver", language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL, duration_minutes=30, total_questions=1,
            rubric_version="v1", question_set_version="v1", is_active=True)
        self.session = InterviewSession.objects.create(
            candidate=self.candidate, config=config, role_name="Child Caregiver", role_code="child_caregiver",
            ui_language="en", candidate_language="EN", evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1", question_set_version="v1", expires_at=InterviewSession.build_expiry(30),
            created_by=self.owner)
        self.client = APIClient()
        self.url = f"/api/v1/interviews/{self.session.public_id}/send-link/"

    def test_owner_sends_the_link_in_the_interview_language(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["sent_to"], "sara@example.com")
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["sara@example.com"])
        # The short link when short links are configured, otherwise the full one.
        self.assertTrue("/s/" in message.body or str(self.session.public_id) in message.body, message.body)
        self.assertIn("Child Caregiver", message.body)

        response = self.client.post(self.url, {"locale": "ar"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("رابط تقييم", mail.outbox[1].subject)

    def test_only_the_owner_can_send(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_ended_assessment_is_refused(self):
        InterviewSession.objects.filter(pk=self.session.pk).update(status=InterviewSessionStatus.COMPLETED,
                                                                   ended_at=timezone.now())
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 400)

    def test_mail_failure_is_reported_not_hidden(self):
        self.client.force_authenticate(self.owner)
        with mock.patch("api.accounts.utils.send_mail", side_effect=OSError("SMTP down")):
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 502)
        self.assertIn("could not be sent", response.data["detail"])
