"""Service incidents (rulebook EXC-04, EXC-06)."""
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.candidates.models import Candidate
from api.core.constants import EvaluationType, InterviewEvaluationTier, Roles
from api.evaluations.models import Evaluation
from api.interviews.models import InterviewConfiguration
from api.monitoring.models import ServiceIncident
from api.monitoring.services import ServiceIncidentService
from api.reports.services import EvaluationReportService
from api.sessions.models import CandidateResponse, InterviewSession
from api.sessions.services import InterviewSessionService, QuestionGenerationService

VERSION = "GOV1.2-FINAL"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEFAULT_FROM_EMAIL="noreply@example.com")
class ServiceIncidentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_governance_question_bank", verbosity=0)
        cls.admin = User.objects.create_user(email="si-admin@example.com", password="x", first_name="A", last_name="D",
                                             role=Roles.ADMIN, is_verified=True)
        cls.owner = User.objects.create_user(email="si-owner@example.com", password="x", first_name="O", last_name="W",
                                             role=Roles.B2B, is_verified=True)
        cls.company = Company.objects.create(name="Incident Co", registration_number="SI-1", company_size="11-50",
                                             phone_number="+1", country="X", city="Y", admin_user=cls.owner,
                                             business_license_verified=True)
        CompanyEmployerProfile.objects.create(user=cls.owner, company_name=cls.company.name, company=cls.company,
                                              company_registration_number="SI-1", company_size="11-50")

    def _session(self, answered=True, processing_status="PROCESSING_COMPLETED"):
        config = InterviewConfiguration.objects.get(role_code="driver", language="EN",
                                                   evaluation_tier=InterviewEvaluationTier.FULL,
                                                   question_set_version=VERSION)
        cand = Candidate.objects.create(first_name="C", last_name="I", email=f"si-{Candidate.objects.count()}@example.com",
                                        passport_id=f"SI{Candidate.objects.count()}", job_role="OT", core_skills="",
                                        preferred_language="EN", passport_document="candidates/documents/passport/t.pdf",
                                        created_by=self.owner, company=self.company)
        session = InterviewSession.objects.create(
            candidate=cand, organization=self.company, config=config, role_name=config.role_name, role_code="driver",
            ui_language="EN", candidate_language="EN", tts_language_code="en-US", stt_language_code="en-US",
            evaluation_tier=InterviewEvaluationTier.FULL, rubric_version=VERSION, question_set_version=VERSION,
            status="COMPLETED", ended_at=timezone.now(), expires_at=InterviewSession.build_expiry(30),
            created_by=self.owner)
        questions = QuestionGenerationService.generate_questions(session)
        Evaluation.objects.create(session=session, candidate=cand, evaluation_type=EvaluationType.INTERVIEW,
                                  status="COMPLETED", scheduled_date=timezone.now(), duration_minutes=45,
                                  created_by=self.owner, company=self.company,
                                  evaluation_tier=InterviewEvaluationTier.FULL)
        if answered:
            CandidateResponse.objects.create(session=session, question=questions[0], transcript="(answer)",
                                             processing_status=processing_status)
        return session

    def test_incident_notifies_admins_once_and_counts_repeats(self):
        session = self._session()
        first = ServiceIncidentService.report(cause=ServiceIncident.CAUSE_REPORT_FAILED, summary="Report failed",
                                              session=session, affected_output="report")
        again = ServiceIncidentService.report(cause=ServiceIncident.CAUSE_REPORT_FAILED, summary="Report failed",
                                              session=session, affected_output="report")
        self.assertEqual(first.pk, again.pk)
        again.refresh_from_db()
        self.assertEqual(again.occurrence_count, 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("si-admin@example.com", mail.outbox[0].to)
        self.assertIsNotNone(again.notified_at)
        self.assertTrue(again.next_action)
        self.assertIsNotNone(again.deadline)
        self.assertEqual(again.company, self.company)
        self.assertEqual(again.evaluation, session.linked_evaluation)

    def test_report_failure_at_completion_opens_an_incident(self):
        # The 19 Oct production report failure was silent: logged nowhere.
        session = self._session(processing_status="FAILED")
        with mock.patch.object(EvaluationReportService, "generate_for_evaluation", side_effect=RuntimeError("boom")):
            InterviewSessionService.generate_completed_session_outputs(session)
        causes = set(ServiceIncident.objects.filter(session=session).values_list("cause", flat=True))
        self.assertEqual(causes, {ServiceIncident.CAUSE_AI_PROCESSING_FAILED, ServiceIncident.CAUSE_REPORT_FAILED})
        # One email per incident.
        self.assertEqual(len(mail.outbox), 2)

    def test_failed_ai_processing_opens_an_incident(self):
        session = self._session(processing_status="FAILED")
        InterviewSessionService._report_failed_ai_processing(session, "C I")
        incident = ServiceIncident.objects.get(session=session)
        self.assertEqual(incident.cause, ServiceIncident.CAUSE_AI_PROCESSING_FAILED)
        self.assertIn("1", incident.details)

    def test_candidate_who_answered_nothing_is_not_an_incident(self):
        session = self._session(answered=False)
        InterviewSessionService.generate_completed_session_outputs(session)
        self.assertFalse(ServiceIncident.objects.filter(session=session).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_api_assigns_and_resolves(self):
        session = self._session()
        incident = ServiceIncidentService.report(cause=ServiceIncident.CAUSE_REPORT_FAILED, summary="Report failed",
                                                 session=session)
        client = APIClient()
        client.force_authenticate(self.admin)
        listed = client.get("/api/v1/monitoring/incidents?status=OPEN")
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(len(listed.data), 1)
        url = f"/api/v1/monitoring/incidents/{incident.public_id}"
        assigned = client.patch(url, {"owner_email": "si-admin@example.com", "status": "IN_PROGRESS"}, format="json")
        self.assertEqual(assigned.status_code, 200, assigned.data)
        self.assertEqual(assigned.data["owner_email"], "si-admin@example.com")
        refused = client.patch(url, {"status": "RESOLVED"}, format="json")
        self.assertEqual(refused.status_code, 400)
        resolved = client.patch(url, {"status": "RESOLVED", "resolution_note": "Regenerated the report."}, format="json")
        self.assertEqual(resolved.status_code, 200, resolved.data)
        incident.refresh_from_db()
        self.assertEqual(incident.resolved_by, self.admin)

    def test_non_admins_cannot_see_incidents(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        self.assertEqual(client.get("/api/v1/monitoring/incidents").status_code, 403)
