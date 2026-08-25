import asyncio
import json
import shutil
import tempfile
from decimal import Decimal
from unittest.mock import patch

import requests

from asgiref.testing import ApplicationCommunicator
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from api.accounts.models import User
from api.audit.models import AuditLog
from api.candidates.models import Candidate
from api.core.constants import AuditLogAction, CoverageLevel, InterviewEvaluationTier, QuestionDifficulty, QuestionLifecycleStatus, Roles
from api.interviews.models import InterviewConfiguration, InterviewRubric, PackageSessionConfig, RolePackageCoverage
from api.interviews.voice_services import VoiceProviderError
from api.payments.models import PackageBalance
from api.questions.models import QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession, ObservedTaskDefinition, SessionArtifact, SessionObservedTask, TaskObservationResult
from api.sessions.services import InterviewSessionService
from api.translation.models import CandidateResponseInterpretation, CandidateResponseTranslation, EvaluationInputArtifact
from api.translation.services import AIProcessingError, AIProcessingOrchestrationService
from api.evaluations.models import Evaluation
from meritlense.asgi import application


def make_file(name="passport.pdf", content=None):
    if content is None:
        content = (
            b"%PDF-1.1\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R>>endobj\n"
            b"4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 20 100 Td (Passport) Tj ET\nendstream\nendobj\n"
            b"xref\n0 5\n0000000000 65535 f \n0000000010 00000 n \n0000000053 00000 n \n0000000104 00000 n \n0000000191 00000 n \n"
            b"trailer<</Root 1 0 R/Size 5>>\nstartxref\n285\n%%EOF\n"
        )
    return SimpleUploadedFile(name, content, content_type="application/pdf")


def make_image(name="photo.jpg", content=b"fake-image"):
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


class InterviewSessionApiTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_dir = tempfile.mkdtemp(prefix="interview-tests-")
        cls._override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            first_name="Owner",
            last_name="User",
            role=Roles.B2C,
            is_verified=True,
        )
        self.client.force_authenticate(self.user)

        self.candidate = Candidate.objects.create(
            first_name="Dawit",
            last_name="Session",
            email="candidate@example.com",
            passport_id="PASS-W3-001",
            job_role="NA",
            core_skills="cleaning,care",
            preferred_language="EN",
            passport_document=make_file(),
            created_by=self.user,
        )
        PackageBalance.objects.create(
            owner_user=self.user,
            balance_type=PackageBalance.SLOTS,
            fixed_amount=1000,
            current_balance=1000,
        )
        self.config = InterviewConfiguration.objects.create(
            role_name="Nanny",
            role_code="nanny",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30,
            total_questions=3,
            allow_retries=True,
            max_retries=1,
            rubric_version="v1",
            question_set_version="v1",
        )
        for index in range(1, 4):
            QuestionTemplate.objects.create(
                role_name="Nanny",
                role_code="nanny",
                question_code=f"NAN-{index:03d}",
                question_version="1.0",
                question_status=QuestionLifecycleStatus.ACTIVE,
                domain="Child Care",
                skill_tag=f"Skill {index}",
                skill=f"Skill {index}",
                sequence_number=index,
                difficulty=QuestionDifficulty.MEDIUM,
                question_text=f"Question text {index}",
                question_type="knowledge",
                question_format="TEXT",
                expected_steps=["step1", "step2"],
                keywords=["care", "safe"],
                language="EN",
                scoring_type="0/3/5",
                difficulty_score=2,
                estimated_time_seconds=30,
                expected_answer_type="structured",
                evaluation_tier=InterviewEvaluationTier.FULL,
                rubric_version="v1",
                question_set_version="v1",
            )

    def test_can_crud_interview_configuration(self):
        response = self.client.post(
            "/api/v1/interviews/configs/",
            {
                "role_name": "Driver",
                "role_code": "driver",
                "language": "EN",
                "evaluation_tier": "FULL",
                "duration_minutes": 25,
                "total_questions": 2,
                "allow_retries": True,
                "max_retries": 1,
                "enable_translation": False,
                "enable_task_module": False,
                "enable_integrity_checks": False,
                "rubric_version": "v1",
                "question_set_version": "v1",
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        config_id = response.data["id"]

        patch_response = self.client.patch(
            f"/api/v1/interviews/configs/{config_id}/",
            {"duration_minutes": 35},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.data["duration_minutes"], 35)

        delete_response = self.client.delete(f"/api/v1/interviews/configs/{config_id}/")
        self.assertEqual(delete_response.status_code, 204)

    def test_can_crud_question_template(self):
        response = self.client.post(
            "/api/v1/interviews/question-templates/",
            {
                "role_name": "Nanny",
                "role_code": "nanny",
                "question_code": "NAN-010",
                "question_version": "1.0",
                "question_status": "active",
                "domain": "Care",
                "skill_tag": "Patience",
                "skill": "Patience",
                "sequence_number": 10,
                "difficulty": "MEDIUM",
                "question_text": "How would you calm a crying child?",
                "question_type": "behavioral",
                "question_format": "SCENARIO",
                "expected_steps": ["stay calm", "reassure"],
                "keywords": ["patience", "calm"],
                "weight": "1.00",
                "language": "EN",
                "scoring_type": "0/3/5",
                "difficulty_score": 2,
                "estimated_time_seconds": 60,
                "expected_answer_type": "structured",
                "evaluation_tier": "FULL",
                "rubric_version": "v1",
                "question_set_version": "v1",
                "is_mandatory": True,
                "follow_up_allowed": False,
                "critical_question": False,
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        template_id = response.data["id"]

        patch_response = self.client.patch(
            f"/api/v1/interviews/question-templates/{template_id}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertFalse(patch_response.data["is_active"])

        delete_response = self.client.delete(f"/api/v1/interviews/question-templates/{template_id}/")
        self.assertEqual(delete_response.status_code, 204)

    def test_question_template_create_normalizes_role_specific_skill_tag(self):
        response = self.client.post(
            "/api/v1/interviews/question-templates/",
            {
                "role_name": "Nursing Assistant",
                "role_code": "nursing_assistant",
                "question_code": "NA-SAF-010",
                "question_version": "1.0",
                "question_status": "active",
                "domain": "Patient Safety",
                "skill_tag": "Patient Safety",
                "skill": "Patient Safety",
                "sequence_number": 10,
                "difficulty": "MEDIUM",
                "question_text": "What would you do first?",
                "question_type": "safety",
                "question_format": "SCENARIO",
                "expected_steps": ["call for help"],
                "keywords": ["safety"],
                "weight": "1.00",
                "language": "EN",
                "scoring_type": "0/3/5",
                "difficulty_score": 2,
                "estimated_time_seconds": 60,
                "expected_answer_type": "structured",
                "evaluation_tier": "FULL",
                "rubric_version": "v2.0",
                "question_set_version": "v1.2",
                "is_mandatory": True,
                "follow_up_allowed": False,
                "critical_question": False,
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        template = QuestionTemplate.objects.get(public_id=response.data["id"])
        self.assertEqual(template.skill_tag, "Safety Awareness")
        self.assertEqual(template.skill, "Safety Awareness")
        self.assertEqual(template.skill_id, "safety_awareness")

    def test_can_crud_interview_rubric(self):
        response = self.client.post(
            "/api/v1/interviews/rubrics/",
            {
                "role_name": "Housekeeper",
                "role_code": "domestic_worker",
                "skill_tag": "Safety Awareness",
                "scoring_category": "Knowledge & Safety",
                "weight": "0.2700",
                "max_score": 40,
                "scoring_type": "0/3/5",
                "domain": "Safety & Hygiene",
                "notes": "Maps to knowledge_score",
                "rubric_version": "v2.0",
                "question_set_version": "v1.0",
                "evaluation_criteria": [{"question_ref": "EN-Q1"}],
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        rubric_id = response.data["id"]
        self.assertEqual(InterviewRubric.objects.count(), 1)

        patch_response = self.client.patch(
            f"/api/v1/interviews/rubrics/{rubric_id}/",
            {"notes": "Updated"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.data["notes"], "Updated")

    def test_interview_rubric_create_normalizes_role_specific_skill_tag(self):
        response = self.client.post(
            "/api/v1/interviews/rubrics/",
            {
                "role_name": "Nursing Assistant",
                "role_code": "nursing_assistant",
                "skill_tag": "Patient Safety",
                "scoring_category": "Patient Safety",
                "weight": "0.3500",
                "max_score": 52,
                "scoring_type": "0/3/5",
                "domain": "Patient Safety",
                "notes": "Maps to knowledge_score",
                "rubric_version": "v2.0",
                "question_set_version": "v1.2",
                "evaluation_criteria": [{"question_ref": "NA-SAF-001"}],
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        rubric = InterviewRubric.objects.get(public_id=response.data["id"])
        self.assertEqual(rubric.skill_tag, "Safety Awareness")
        self.assertEqual(rubric.scoring_category, "Safety Awareness")

    def test_can_crud_package_session_config_and_role_coverage(self):
        package_response = self.client.post(
            "/api/v1/interviews/package-configs/",
            {
                "package_code": "starter",
                "package_name": "Starter",
                "audience": "B2B",
                "evaluation_tier": "SCREENING",
                "min_questions": 5,
                "max_questions": 8,
                "default_question_count": 8,
                "duration_minutes": 15,
                "task_observation_enabled": False,
                "readiness_indicator_enabled": False,
                "certificate_enabled": False,
                "basic_report_enabled": True,
                "analytics_enabled": False,
                "api_access_enabled": False,
                "video_introduction_enabled": False,
                "behavioral_indicators_enabled": False,
                "points_balance": 100,
                "monthly_fee_display": "Pilot pricing",
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(package_response.status_code, 201, package_response.data)
        package_id = package_response.data["id"]

        coverage_response = self.client.post(
            "/api/v1/interviews/role-coverage/",
            {
                "role_name": "Nanny",
                "role_code": "nanny",
                "package_code": "starter",
                "package_name": "Starter",
                "audience": "B2B",
                "coverage_level": "SCREENING",
                "evaluation_tier": "SCREENING",
                "readiness_indicator_enabled": False,
                "certificate_enabled": False,
                "video_introduction_enabled": False,
                "behavioral_indicators_enabled": False,
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(coverage_response.status_code, 201, coverage_response.data)

        list_packages = self.client.get("/api/v1/interviews/package-configs/")
        list_coverage = self.client.get("/api/v1/interviews/role-coverage/")
        self.assertEqual(list_packages.status_code, 200)
        self.assertEqual(list_coverage.status_code, 200)
        self.assertTrue(any(item["id"] == package_id for item in list_packages.data))

    def test_team_member_cannot_manage_interview_setup(self):
        team_member = User.objects.create_user(
            email="team@example.com",
            password="testpass123",
            first_name="Team",
            last_name="Member",
            role=Roles.B2B_TEAM_MEMBER,
            is_verified=True,
        )
        self.client.force_authenticate(team_member)

        response = self.client.post(
            "/api/v1/interviews/configs/",
            {
                "role_name": "Driver",
                "role_code": "driver",
                "language": "EN",
                "evaluation_tier": "FULL",
                "duration_minutes": 25,
                "total_questions": 2,
                "allow_retries": False,
                "max_retries": 0,
                "enable_translation": False,
                "enable_task_module": False,
                "enable_integrity_checks": False,
                "rubric_version": "v1",
                "question_set_version": "v1",
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_starting_session_blocks_when_no_slots_remain(self):
        PackageBalance.objects.filter(owner_user=self.user, balance_type=PackageBalance.SLOTS).update(current_balance=0)

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        session_id = create_response.data["id"]

        start_response = self.client.post(
            f"/api/v1/interviews/{session_id}/start/",
            {},
            format="json",
        )
        self.assertEqual(start_response.status_code, 400)
        self.assertIn("slots remaining", str(start_response.data["detail"]).lower())

        session = InterviewSession.objects.get(public_id=session_id)
        self.assertNotEqual(session.status, "IN_PROGRESS")

    def test_starting_session_consumes_exactly_one_slot(self):
        PackageBalance.objects.filter(owner_user=self.user, balance_type=PackageBalance.SLOTS).update(current_balance=1)

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        session_id = create_response.data["id"]

        start_response = self.client.post(
            f"/api/v1/interviews/{session_id}/start/",
            {},
            format="json",
        )
        self.assertEqual(start_response.status_code, 200)

        balance = PackageBalance.objects.get(owner_user=self.user, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 0)

    def test_create_start_answer_and_complete_interview_session(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.data["status"], "CREATED")
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]

        start_response = self.client.post(
            f"/api/v1/interviews/{session_id}/start/",
            {},
            format="json",
        )
        self.assertEqual(start_response.status_code, 200)
        self.assertEqual(start_response.data["status"], "IN_PROGRESS")

        next_question = self.client.get(f"/api/v1/interviews/{session_id}/next-question/")
        self.assertEqual(next_question.status_code, 200)
        question_id = next_question.data["id"]

        answer_response = self.client.post(
            f"/api/v1/interviews/{session_id}/submit-response/",
            {
                "question_id": question_id,
                "transcript": "I would keep the child safe first.",
                "text_response": "I would keep the child safe first.",
            },
            format="json",
        )
        self.assertEqual(answer_response.status_code, 200)
        self.assertEqual(answer_response.data["status"], "SUCCESS")
        self.assertEqual(CandidateResponse.objects.count(), 1)

        token_client = APIClient()
        retrieve_response = token_client.get(
            f"/api/v1/interviews/{session_id}/",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(retrieve_response.data["id"], session_id)

        complete_response = token_client.post(
            f"/api/v1/interviews/{session_id}/complete/",
            {"token": access_token},
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(complete_response.status_code, 200)
        self.assertEqual(complete_response.data["status"], "COMPLETED")

    def test_create_session_with_schedule_blocks_early_start_and_syncs_evaluation(self):
        scheduled_start_at = timezone.now() + timezone.timedelta(days=2)
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
                "scheduled_start_at": scheduled_start_at.isoformat(),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        self.assertEqual(create_response.data["status"], "CREATED")
        self.assertIsNotNone(create_response.data["scheduled_start_at"])

        session = InterviewSession.objects.get(public_id=create_response.data["id"])
        self.assertEqual(session.scheduled_start_at.isoformat(), scheduled_start_at.isoformat())
        self.assertGreater(session.expires_at, scheduled_start_at)

        evaluation = session.linked_evaluation
        self.assertEqual(evaluation.scheduled_date.isoformat(), scheduled_start_at.isoformat())
        self.assertEqual(evaluation.status, "SCHEDULED")

        start_response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/start/",
            {},
            format="json",
        )
        self.assertEqual(start_response.status_code, 400)
        self.assertIn("scheduled start time", str(start_response.data["detail"]).lower())

    def test_can_reschedule_pending_session(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        rescheduled_at = timezone.now() + timezone.timedelta(days=3)

        response = self.client.post(
            f"/api/v1/interviews/{session_id}/reschedule/",
            {
                "scheduled_start_at": rescheduled_at.isoformat(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNotNone(response.data["scheduled_start_at"])

        session = InterviewSession.objects.get(public_id=session_id)
        self.assertEqual(session.scheduled_start_at.isoformat(), rescheduled_at.isoformat())
        self.assertEqual(session.linked_evaluation.scheduled_date.isoformat(), rescheduled_at.isoformat())

    def test_upcoming_filter_returns_only_future_scheduled_sessions(self):
        future = InterviewSessionService.create_session(
            candidate=self.candidate,
            config=self.config,
            created_by=self.user,
            scheduled_start_at=timezone.now() + timezone.timedelta(days=2),
        )
        InterviewSessionService.create_session(
            candidate=self.candidate,
            config=self.config,
            created_by=self.user,
        )

        response = self.client.get("/api/v1/interviews/?upcoming=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data], [str(future.public_id)])

    def test_can_cancel_pending_session_and_linked_evaluation(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]

        response = self.client.post(
            f"/api/v1/interviews/{session_id}/cancel/",
            {
                "reason": "Candidate requested another date",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "CANCELLED")
        self.assertEqual(response.data["cancellation_reason"], "Candidate requested another date")

        session = InterviewSession.objects.get(public_id=session_id)
        self.assertEqual(session.status, "CANCELLED")
        self.assertIsNotNone(session.cancelled_at)
        self.assertEqual(session.cancellation_reason, "Candidate requested another date")
        self.assertEqual(session.linked_evaluation.status, "CANCELLED")
        self.assertEqual(session.linked_evaluation.cancellation_reason, "Candidate requested another date")

    def test_staff_starting_a_session_does_not_fake_identity_verification(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]

        start_response = self.client.post(f"/api/v1/interviews/{session_id}/start/", {}, format="json")

        self.assertEqual(start_response.status_code, 200, start_response.data)
        self.assertEqual(start_response.data["status"], "IN_PROGRESS")
        session = InterviewSession.objects.get(public_id=session_id)
        self.assertFalse(session.identity_verified)
        self.assertNotEqual(session.verification_status, "VERIFIED")
        self.assertFalse(session.single_face_detected)
        self.assertFalse(session.integrity_logs.filter(event_type="MANUAL_VERIFICATION_OVERRIDE").exists())

    def test_candidate_precheck_flow_supports_token_start(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        consent = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/consent/",
            {
                "token": access_token,
                "signatory_name": "Dawit Session",
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(consent.status_code, 200, consent.data)

        privacy = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/privacy-acknowledgement/",
            {
                "token": access_token,
                "metadata": {"screen": "candidate-start"},
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(privacy.status_code, 200, privacy.data)

        device = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/device-check/",
            {
                "token": access_token,
                "passed": True,
                "metadata": {"camera": "ok", "microphone": "ok"},
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(device.status_code, 200, device.data)

        verbal = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/verbal-confirmation/",
            {
                "token": access_token,
                "recording_path": "candidate/verbal-confirmation.webm",
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(verbal.status_code, 200, verbal.data)

        identity = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/identity-verify/",
            {
                "token": access_token,
                "face_match_score": "93.50",
                "single_face_detected": True,
                "liveness_passed": True,
                "metadata": {"source": "candidate-ui"},
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(identity.status_code, 200, identity.data)
        self.assertTrue(identity.data["verification"]["identity_verified"])
        self.assertEqual(identity.data["precheck_status"]["status"], "READY")

        start_response = token_client.post(
            f"/api/v1/interviews/{session_id}/start/",
            {"token": access_token},
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(start_response.status_code, 200, start_response.data)
        self.assertEqual(start_response.data["status"], "IN_PROGRESS")

        session = InterviewSession.objects.get(public_id=session_id)
        self.assertTrue(session.identity_verified)
        self.assertEqual(str(session.face_match_score), "93.50")
        self.assertTrue(session.candidate_prechecks_complete())
        self.assertIsNotNone(session.candidate_consent_agreement_id)
        self.assertIsNotNone(session.privacy_notice_acknowledged_at)
        self.assertIsNotNone(session.device_check_completed_at)
        self.assertIsNotNone(session.verbal_confirmation_recorded_at)

    def test_candidate_precheck_flow_completes_without_consent(self):
        # Consent capture was removed from the required precheck gate as a
        # deliberate product decision - candidates no longer need to sign
        # anything before completing the other prechecks and starting.
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        privacy = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/privacy-acknowledgement/",
            {
                "token": access_token,
                "metadata": {"screen": "candidate-start"},
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(privacy.status_code, 200, privacy.data)

        device = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/device-check/",
            {
                "token": access_token,
                "passed": True,
                "metadata": {"camera": "ok", "microphone": "ok"},
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(device.status_code, 200, device.data)

        verbal = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/verbal-confirmation/",
            {
                "token": access_token,
                "recording_path": "candidate/verbal-confirmation.webm",
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(verbal.status_code, 200, verbal.data)

        identity = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/identity-verify/",
            {
                "token": access_token,
                "face_match_score": "93.50",
                "single_face_detected": True,
                "liveness_passed": True,
                "metadata": {"source": "candidate-ui"},
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(identity.status_code, 200, identity.data)

        start_response = token_client.post(
            f"/api/v1/interviews/{session_id}/start/",
            {"token": access_token},
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )
        self.assertEqual(start_response.status_code, 200, start_response.data)
        self.assertEqual(start_response.data["status"], "IN_PROGRESS")

        session = InterviewSession.objects.get(public_id=session_id)
        self.assertTrue(session.candidate_prechecks_complete())
        self.assertIsNone(session.candidate_consent_agreement_id)

    def test_identity_verification_accepts_provider_result_payload(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        response = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/identity-verify/",
            {
                "token": access_token,
                "provider_result": {
                    "provider": "AZURE_FACE",
                    "verification_status": "VERIFIED",
                    "face_match_score": 96.2,
                    "single_face_detected": True,
                    "liveness_passed": True,
                    "reason": "Provider face match passed",
                },
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["verification"]["provider"], "AZURE_FACE")
        self.assertTrue(response.data["verification"]["identity_verified"])
        session = InterviewSession.objects.get(public_id=session_id)
        self.assertEqual(session.verification_status, "VERIFIED")
        self.assertEqual(str(session.face_match_score), "96.20")

    @override_settings(
        IDENTITY_VERIFICATION_PROVIDER="AZURE_FACE",
        IDENTITY_VERIFICATION_API_URL="https://identity-provider.example/verify",
        IDENTITY_VERIFICATION_API_KEY="test-key",
    )
    def test_identity_verification_reuses_candidate_passport_when_no_fresh_id_uploaded(self):
        self.candidate.passport_document.save("passport-reference.jpg", ContentFile(b"passport-image"), save=True)

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        captured = {}

        def fake_provider_call(provider_self, *, session, metadata=None, id_document_file=None, selfie_file=None):
            captured["id_document_name"] = getattr(id_document_file, "name", None)
            captured["selfie_name"] = getattr(selfie_file, "name", None)
            captured["metadata"] = metadata or {}
            return provider_self._normalize_result(
                {
                    "provider": "AZURE_FACE",
                    "face_match_score": 94.1,
                    "single_face_detected": True,
                    "liveness_passed": True,
                },
                metadata=metadata,
            )

        with patch(
            "api.sessions.identity_services.DynamicIdentityVerificationProvider._call_remote_provider",
            autospec=True,
            side_effect=fake_provider_call,
        ):
            response = token_client.post(
                f"/api/v1/interviews/{session_id}/prechecks/identity-verify/",
                {
                    "token": access_token,
                    "selfie_image_file": make_image("live-selfie.jpg", b"selfie-bytes"),
                },
                format="multipart",
                HTTP_X_SESSION_TOKEN=access_token,
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["verification"]["identity_verified"])
        self.assertIn("passport-reference.jpg", captured["id_document_name"])
        self.assertEqual(captured["selfie_name"], "live-selfie.jpg")
        self.assertEqual(captured["metadata"]["reference_document_source"], "candidate_passport_document")
        self.assertTrue(captured["metadata"]["reused_candidate_passport"])
        self.assertEqual(captured["metadata"]["provider_reference_source"], "candidate_passport_document")
        session = InterviewSession.objects.get(public_id=session_id)
        self.assertTrue(session.identity_verified)
        self.assertFalse(SessionArtifact.objects.filter(session=session, artifact_type="ID_DOCUMENT").exists())
        self.assertTrue(SessionArtifact.objects.filter(session=session, artifact_type="SELFIE_IMAGE").exists())

    @override_settings(
        IDENTITY_VERIFICATION_PROVIDER="AZURE_FACE",
        IDENTITY_VERIFICATION_API_URL="https://identity-provider.example/verify",
        IDENTITY_VERIFICATION_API_KEY="test-key",
    )
    def test_identity_verification_converts_stored_pdf_passport_for_provider_flow(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        captured = {}

        def fake_provider_call(provider_self, *, session, metadata=None, id_document_file=None, selfie_file=None):
            captured["id_document_name"] = getattr(id_document_file, "name", None)
            captured["id_document_type"] = getattr(id_document_file, "content_type", None)
            captured["metadata"] = metadata or {}
            return provider_self._normalize_result(
                {
                    "provider": "AZURE_FACE",
                    "face_match_score": 96.0,
                    "single_face_detected": True,
                    "liveness_passed": True,
                },
                metadata=metadata,
            )

        with patch(
            "api.sessions.identity_services.DynamicIdentityVerificationProvider._call_remote_provider",
            autospec=True,
            side_effect=fake_provider_call,
        ):
            response = token_client.post(
                f"/api/v1/interviews/{session_id}/prechecks/identity-verify/",
                {
                    "token": access_token,
                    "selfie_image_file": make_image("live-selfie.jpg", b"selfie-bytes"),
                },
                format="multipart",
                HTTP_X_SESSION_TOKEN=access_token,
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(captured["id_document_name"].endswith(".png"))
        self.assertEqual(captured["id_document_type"], "image/png")
        self.assertEqual(captured["metadata"]["provider_reference_source"], "pdf_first_page_image")
        self.assertEqual(captured["metadata"]["provider_reference_fallback_reason"], "pdf_reference_converted_to_image")

    @override_settings(
        IDENTITY_VERIFICATION_PROVIDER="AZURE_FACE",
        IDENTITY_VERIFICATION_API_URL="https://identity-provider.example/verify",
        IDENTITY_VERIFICATION_API_KEY="test-key",
    )
    def test_identity_verification_uses_profile_photo_when_pdf_conversion_fails(self):
        self.candidate.profile_photo.save("candidate-profile.jpg", ContentFile(b"profile-image"), save=True)
        self.candidate.passport_document.save("broken-passport.pdf", ContentFile(b"not-a-real-pdf"), save=True)

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        captured = {}

        def fake_provider_call(provider_self, *, session, metadata=None, id_document_file=None, selfie_file=None):
            captured["id_document_name"] = getattr(id_document_file, "name", None)
            captured["metadata"] = metadata or {}
            return provider_self._normalize_result(
                {
                    "provider": "AZURE_FACE",
                    "face_match_score": 96.0,
                    "single_face_detected": True,
                    "liveness_passed": True,
                },
                metadata=metadata,
            )

        with patch(
            "api.sessions.identity_services.DynamicIdentityVerificationProvider._call_remote_provider",
            autospec=True,
            side_effect=fake_provider_call,
        ):
            response = token_client.post(
                f"/api/v1/interviews/{session_id}/prechecks/identity-verify/",
                {
                    "token": access_token,
                    "selfie_image_file": make_image("live-selfie.jpg", b"selfie-bytes"),
                },
                format="multipart",
                HTTP_X_SESSION_TOKEN=access_token,
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("candidate-profile.jpg", captured["id_document_name"])
        self.assertEqual(captured["metadata"]["reference_document_source"], "candidate_passport_document")
        self.assertEqual(captured["metadata"]["provider_reference_source"], "candidate_profile_photo")
        self.assertEqual(captured["metadata"]["provider_reference_fallback_reason"], "pdf_conversion_failed")
        self.assertIn("pdf_conversion_error", captured["metadata"])

    def test_identity_verification_preserves_zero_face_match_score(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        response = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/identity-verify/",
            {
                "token": access_token,
                "selfie_image_file": make_image("live-selfie.jpg", b"selfie-bytes"),
                "face_match_score": "0",
                "single_face_detected": "false",
            },
            format="multipart",
            HTTP_X_SESSION_TOKEN=access_token,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["verification"]["face_match_score"], "0.00")
        self.assertFalse(response.data["verification"]["identity_verified"])
        session = InterviewSession.objects.get(public_id=session_id)
        self.assertEqual(session.face_match_score, Decimal("0.00"))

    @override_settings(
        IDENTITY_VERIFICATION_PROVIDER="AZURE_FACE",
        IDENTITY_VERIFICATION_API_URL="https://identity-provider.example/verify",
        IDENTITY_VERIFICATION_API_KEY="test-key",
    )
    def test_identity_verification_rejects_pdf_passport_when_conversion_fails_and_no_fallback_exists(self):
        self.candidate.passport_document.save("broken-passport.pdf", ContentFile(b"not-a-real-pdf"), save=True)

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        response = token_client.post(
            f"/api/v1/interviews/{session_id}/prechecks/identity-verify/",
            {
                "token": access_token,
                "selfie_image_file": make_image("live-selfie.jpg", b"selfie-bytes"),
            },
            format="multipart",
            HTTP_X_SESSION_TOKEN=access_token,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Passport PDF could not be converted", str(response.data["detail"]))

    def test_reference_image_endpoint_returns_image_passport_directly(self):
        self.candidate.passport_document.save("passport-reference.jpg", ContentFile(b"passport-image-bytes"), save=True)

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        response = token_client.get(
            f"/api/v1/interviews/{session_id}/prechecks/reference-image/",
            {"token": access_token},
            HTTP_X_SESSION_TOKEN=access_token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(b"".join(response.streaming_content), b"passport-image-bytes")

    def test_reference_image_endpoint_prefers_verification_photo_over_passport(self):
        self.candidate.passport_document.save("passport-reference.jpg", ContentFile(b"passport-image-bytes"), save=True)
        self.candidate.verification_photo.save("verification-crop.jpg", ContentFile(b"cropped-face-bytes"), save=True)

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        response = token_client.get(
            f"/api/v1/interviews/{session_id}/prechecks/reference-image/",
            {"token": access_token},
            HTTP_X_SESSION_TOKEN=access_token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"cropped-face-bytes")

    def test_reference_image_endpoint_converts_pdf_passport_to_png(self):
        self.candidate.passport_document.save("passport.pdf", make_file(), save=True)

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        response = token_client.get(
            f"/api/v1/interviews/{session_id}/prechecks/reference-image/",
            {"token": access_token},
            HTTP_X_SESSION_TOKEN=access_token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_reference_image_endpoint_falls_back_to_profile_photo_when_pdf_conversion_fails(self):
        self.candidate.passport_document.save("broken-passport.pdf", ContentFile(b"not-a-real-pdf"), save=True)
        self.candidate.profile_photo.save("candidate-profile.jpg", ContentFile(b"profile-image-bytes"), save=True)

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        response = token_client.get(
            f"/api/v1/interviews/{session_id}/prechecks/reference-image/",
            {"token": access_token},
            HTTP_X_SESSION_TOKEN=access_token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"profile-image-bytes")

    def test_reference_image_endpoint_returns_404_when_conversion_fails_and_no_fallback(self):
        self.candidate.passport_document.save("broken-passport.pdf", ContentFile(b"not-a-real-pdf"), save=True)

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        response = token_client.get(
            f"/api/v1/interviews/{session_id}/prechecks/reference-image/",
            {"token": access_token},
            HTTP_X_SESSION_TOKEN=access_token,
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("Passport PDF could not be converted", str(response.data["detail"]))

    def test_reference_image_endpoint_rejects_missing_or_invalid_token(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        token_client = APIClient()

        response = token_client.get(f"/api/v1/interviews/{session_id}/prechecks/reference-image/")

        self.assertEqual(response.status_code, 403)

    def test_integrity_event_endpoint_records_webcam_frame_artifact(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        response = token_client.post(
            f"/api/v1/interviews/{session_id}/integrity-events/",
            {
                "token": access_token,
                "event_type": "MULTIPLE_FACES_DETECTED",
                "severity": "WARNING",
                "frame_file": make_image("frame.jpg", b"frame-bytes"),
            },
            format="multipart",
            HTTP_X_SESSION_TOKEN=access_token,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "RECORDED")
        self.assertEqual(response.data["integrity_event"]["event_type"], "MULTIPLE_FACES_DETECTED")

        session = InterviewSession.objects.get(public_id=session_id)
        latest_log = session.integrity_logs.first()
        self.assertIsNotNone(latest_log)
        self.assertEqual(latest_log.event_type, "MULTIPLE_FACES_DETECTED")

        artifact = SessionArtifact.objects.get(session=session, artifact_type="WEBCAM_FRAME")
        self.assertEqual(artifact.mime_type, "image/jpeg")
        self.assertEqual(artifact.file_size_bytes, len(b"frame-bytes"))

    def test_integrity_event_can_auto_classify_multiple_faces_from_provider_payload(self):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        access_token = create_response.data["access_token"]
        token_client = APIClient()

        response = token_client.post(
            f"/api/v1/interviews/{session_id}/integrity-events/",
            {
                "token": access_token,
                "auto_analyze": True,
                "provider_result": {
                    "provider": "AZURE_FACE",
                    "face_count": 2,
                    "liveness_passed": True,
                },
            },
            format="json",
            HTTP_X_SESSION_TOKEN=access_token,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["analysis"]["event_type"], "MULTIPLE_FACES_DETECTED")
        self.assertEqual(response.data["integrity_event"]["event_type"], "MULTIPLE_FACES_DETECTED")
        session = InterviewSession.objects.get(public_id=session_id)
        latest_log = session.integrity_logs.first()
        self.assertEqual(latest_log.event_type, "MULTIPLE_FACES_DETECTED")
        self.assertFalse(session.single_face_detected)

    def _post_integrity_event(self, session, event_type):
        token_client = APIClient()
        return token_client.post(
            f"/api/v1/interviews/{session.public_id}/integrity-events/",
            {
                "token": session.access_token,
                "event_type": event_type,
                "severity": "WARNING" if event_type in ("MULTIPLE_FACES_DETECTED", "CAMERA_UNAVAILABLE", "NO_FACE_DETECTED") else "INFO",
            },
            format="json",
            HTTP_X_SESSION_TOKEN=session.access_token,
        )

    def test_first_multiple_faces_onset_pauses_session_and_blocks_questions(self):
        session = self._create_and_start_session()

        response = self._post_integrity_event(session, "MULTIPLE_FACES_DETECTED")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["session_status"], "PAUSED")
        self.assertEqual(response.data["integrity_violation_count"], 1)
        session.refresh_from_db()
        self.assertEqual(session.status, "PAUSED")
        self.assertEqual(session.integrity_violation_count, 1)
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.SESSION_PAUSED, resource_id=session.id).exists())

        blocked = APIClient().get(
            f"/api/v1/interviews/{session.public_id}/current-question/",
            {"token": session.access_token},
            HTTP_X_SESSION_TOKEN=session.access_token,
        )
        self.assertEqual(blocked.status_code, 400)

    def test_sustained_multiple_faces_does_not_double_count(self):
        session = self._create_and_start_session()

        self._post_integrity_event(session, "MULTIPLE_FACES_DETECTED")
        second = self._post_integrity_event(session, "MULTIPLE_FACES_DETECTED")

        self.assertEqual(second.data["integrity_violation_count"], 1)
        session.refresh_from_db()
        self.assertEqual(session.integrity_violation_count, 1)
        self.assertEqual(session.status, "PAUSED")

    def test_single_face_confirmed_resumes_paused_session(self):
        session = self._create_and_start_session()
        self._post_integrity_event(session, "MULTIPLE_FACES_DETECTED")

        response = self._post_integrity_event(session, "SINGLE_FACE_CONFIRMED")

        self.assertEqual(response.data["session_status"], "IN_PROGRESS")
        session.refresh_from_db()
        self.assertEqual(session.status, "IN_PROGRESS")
        self.assertIsNone(session.paused_at)
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.SESSION_RESUMED, resource_id=session.id).exists())

        resumed = APIClient().get(
            f"/api/v1/interviews/{session.public_id}/current-question/",
            {"token": session.access_token},
            HTTP_X_SESSION_TOKEN=session.access_token,
        )
        self.assertEqual(resumed.status_code, 200)

    def test_third_multiple_faces_onset_terminates_session(self):
        session = self._create_and_start_session()

        self._post_integrity_event(session, "MULTIPLE_FACES_DETECTED")
        self._post_integrity_event(session, "SINGLE_FACE_CONFIRMED")
        self._post_integrity_event(session, "MULTIPLE_FACES_DETECTED")
        self._post_integrity_event(session, "SINGLE_FACE_CONFIRMED")
        third = self._post_integrity_event(session, "MULTIPLE_FACES_DETECTED")

        self.assertEqual(third.data["session_status"], "FAILED")
        self.assertEqual(third.data["integrity_violation_count"], 3)
        session.refresh_from_db()
        self.assertEqual(session.status, "FAILED")
        self.assertTrue(session.is_closed())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.SESSION_FAILED, resource_id=session.id).exists())

        blocked = APIClient().get(
            f"/api/v1/interviews/{session.public_id}/current-question/",
            {"token": session.access_token},
            HTTP_X_SESSION_TOKEN=session.access_token,
        )
        self.assertEqual(blocked.status_code, 400)

    def test_first_camera_unavailable_onset_pauses_session(self):
        session = self._create_and_start_session()

        response = self._post_integrity_event(session, "CAMERA_UNAVAILABLE")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["session_status"], "PAUSED")
        self.assertEqual(response.data["integrity_violation_count"], 1)
        session.refresh_from_db()
        self.assertEqual(session.status, "PAUSED")
        self.assertEqual(session.integrity_violation_count, 1)
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.SESSION_PAUSED, resource_id=session.id).exists())

    def test_sustained_camera_unavailable_does_not_double_count(self):
        session = self._create_and_start_session()

        self._post_integrity_event(session, "CAMERA_UNAVAILABLE")
        second = self._post_integrity_event(session, "CAMERA_UNAVAILABLE")

        self.assertEqual(second.data["integrity_violation_count"], 1)
        session.refresh_from_db()
        self.assertEqual(session.integrity_violation_count, 1)
        self.assertEqual(session.status, "PAUSED")

    def test_camera_back_on_with_single_face_resumes_paused_session(self):
        session = self._create_and_start_session()
        self._post_integrity_event(session, "CAMERA_UNAVAILABLE")

        response = self._post_integrity_event(session, "SINGLE_FACE_CONFIRMED")

        self.assertEqual(response.data["session_status"], "IN_PROGRESS")
        session.refresh_from_db()
        self.assertEqual(session.status, "IN_PROGRESS")

    def test_third_camera_unavailable_onset_terminates_session(self):
        session = self._create_and_start_session()

        self._post_integrity_event(session, "CAMERA_UNAVAILABLE")
        self._post_integrity_event(session, "SINGLE_FACE_CONFIRMED")
        self._post_integrity_event(session, "CAMERA_UNAVAILABLE")
        self._post_integrity_event(session, "SINGLE_FACE_CONFIRMED")
        third = self._post_integrity_event(session, "CAMERA_UNAVAILABLE")

        self.assertEqual(third.data["session_status"], "FAILED")
        self.assertEqual(third.data["integrity_violation_count"], 3)
        session.refresh_from_db()
        self.assertEqual(session.status, "FAILED")
        self.assertTrue(session.is_closed())

    def test_camera_unavailable_then_multiple_faces_both_count_as_new_onsets(self):
        session = self._create_and_start_session()

        self._post_integrity_event(session, "CAMERA_UNAVAILABLE")
        self._post_integrity_event(session, "SINGLE_FACE_CONFIRMED")
        second = self._post_integrity_event(session, "MULTIPLE_FACES_DETECTED")

        # Switching from one escalating problem to a different one is still
        # a new onset, not a debounced repeat of the same sustained issue.
        self.assertEqual(second.data["integrity_violation_count"], 2)
        session.refresh_from_db()
        self.assertEqual(session.integrity_violation_count, 2)
        self.assertEqual(session.status, "PAUSED")

    def test_first_no_face_onset_pauses_session(self):
        session = self._create_and_start_session()

        response = self._post_integrity_event(session, "NO_FACE_DETECTED")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["session_status"], "PAUSED")
        self.assertEqual(response.data["integrity_violation_count"], 1)
        session.refresh_from_db()
        self.assertEqual(session.status, "PAUSED")

    def test_sustained_no_face_does_not_double_count(self):
        session = self._create_and_start_session()

        self._post_integrity_event(session, "NO_FACE_DETECTED")
        second = self._post_integrity_event(session, "NO_FACE_DETECTED")

        self.assertEqual(second.data["integrity_violation_count"], 1)
        session.refresh_from_db()
        self.assertEqual(session.integrity_violation_count, 1)

    def test_third_no_face_onset_terminates_session(self):
        session = self._create_and_start_session()

        self._post_integrity_event(session, "NO_FACE_DETECTED")
        self._post_integrity_event(session, "SINGLE_FACE_CONFIRMED")
        self._post_integrity_event(session, "NO_FACE_DETECTED")
        self._post_integrity_event(session, "SINGLE_FACE_CONFIRMED")
        third = self._post_integrity_event(session, "NO_FACE_DETECTED")

        self.assertEqual(third.data["session_status"], "FAILED")
        self.assertEqual(third.data["integrity_violation_count"], 3)
        session.refresh_from_db()
        self.assertEqual(session.status, "FAILED")

    @patch("api.translation.services.AIProcessingOrchestrationService.auto_process_response_ai")
    def test_submit_response_triggers_automatic_ai_processing_for_text_answers(self, auto_process_mock):
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )
        session_id = create_response.data["id"]
        self.client.post(f"/api/v1/interviews/{session_id}/start/", {}, format="json")

        next_question = self.client.get(f"/api/v1/interviews/{session_id}/next-question/")
        question_id = next_question.data["id"]

        answer_response = self.client.post(
            f"/api/v1/interviews/{session_id}/submit-response/",
            {
                "question_id": question_id,
                "transcript": "I would keep the child safe first.",
                "text_response": "I would keep the child safe first.",
            },
            format="json",
        )

        self.assertEqual(answer_response.status_code, 200, answer_response.data)
        stored = CandidateResponse.objects.get(public_id=answer_response.data["response_id"])
        self.assertEqual(stored.original_transcript, "I would keep the child safe first.")
        self.assertEqual(stored.transcript_language, "EN")
        self.assertEqual(stored.stt_status, "COMPLETED")
        self.assertEqual(stored.processing_status, "TRANSCRIPT_READY")
        auto_process_mock.assert_called_once()
        self.assertEqual(auto_process_mock.call_args.kwargs["response"].id, stored.id)

    def test_submit_response_stores_candidate_selected_answer_language(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)

        answer_response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/submit-response/",
            {
                "question_id": str(question.public_id),
                "transcript": "Sadarka koo jira.",
                "text_response": "Sadarka koo jira.",
                "language_code": "ar-SA",
            },
            format="json",
        )

        self.assertEqual(answer_response.status_code, 200, answer_response.data)
        stored = CandidateResponse.objects.get(public_id=answer_response.data["response_id"])
        self.assertEqual(stored.transcript_language, "ar-SA")

    def test_submit_response_rejects_unsupported_answer_language(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)

        answer_response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/submit-response/",
            {
                "question_id": str(question.public_id),
                "transcript": "hello",
                "language_code": "fr-FR",
            },
            format="json",
        )

        self.assertEqual(answer_response.status_code, 400, answer_response.data)
        self.assertFalse(CandidateResponse.objects.filter(question=question).exists())

    def test_create_session_applies_package_architecture_context(self):
        package = PackageSessionConfig.objects.create(
            package_code="basic",
            package_name="Basic",
            audience="B2C",
            evaluation_tier=InterviewEvaluationTier.SCREENING,
            min_questions=5,
            max_questions=8,
            default_question_count=8,
            duration_minutes=15,
            task_observation_enabled=False,
            readiness_indicator_enabled=False,
            certificate_enabled=False,
            basic_report_enabled=True,
        )
        RolePackageCoverage.objects.create(
            role_name="Nanny",
            role_code="nanny",
            package_code="basic",
            package_name="Basic",
            audience="B2C",
            coverage_level=CoverageLevel.SCREENING,
            evaluation_tier=InterviewEvaluationTier.SCREENING,
            readiness_indicator_enabled=False,
            certificate_enabled=False,
        )

        response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
                "package_code": "basic",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        session = InterviewSession.objects.get(public_id=response.data["id"])
        self.assertEqual(session.package_session_config, package)
        self.assertEqual(session.package_code, "basic")
        self.assertEqual(session.coverage_level, CoverageLevel.SCREENING)
        self.assertEqual(session.evaluation_tier, InterviewEvaluationTier.SCREENING)
        self.assertFalse(session.readiness_indicator_enabled)
        self.assertFalse(session.certificate_enabled)

    def test_session_completion_creates_linked_evaluation_with_package_flags(self):
        package = PackageSessionConfig.objects.create(
            package_code="advanced",
            package_name="Advanced",
            audience="B2C",
            evaluation_tier=InterviewEvaluationTier.FULL,
            min_questions=10,
            max_questions=12,
            default_question_count=12,
            duration_minutes=30,
            task_observation_enabled=True,
            readiness_indicator_enabled=True,
            certificate_enabled=True,
            basic_report_enabled=True,
        )
        RolePackageCoverage.objects.create(
            role_name="Nanny",
            role_code="nanny",
            package_code="advanced",
            package_name="Advanced",
            audience="B2C",
            coverage_level=CoverageLevel.FULL,
            evaluation_tier=InterviewEvaluationTier.FULL,
            readiness_indicator_enabled=True,
            certificate_enabled=True,
            video_introduction_enabled=False,
            behavioral_indicators_enabled=False,
        )

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
                "package_code": "advanced",
            },
            format="json",
        )
        session_id = create_response.data["id"]
        self.client.post(f"/api/v1/interviews/{session_id}/start/", {}, format="json")
        session = InterviewSession.objects.get(public_id=session_id)
        question = self._mark_first_question_asked(session)
        self.client.post(
            f"/api/v1/interviews/{session_id}/submit-response/",
            {
                "question_id": str(question.public_id),
                "transcript": "Answer one",
                "text_response": "Answer one",
            },
            format="json",
        )
        for remaining in session.questions.filter(status="PENDING").order_by("question_order"):
            remaining.status = "ASKED"
            remaining.asked_at = timezone.now()
            remaining.save(update_fields=["status", "asked_at", "updated_at"])
            self.client.post(
                f"/api/v1/interviews/{session_id}/submit-response/",
                {
                    "question_id": str(remaining.public_id),
                    "transcript": f"Answer {remaining.question_order}",
                    "text_response": f"Answer {remaining.question_order}",
                },
                format="json",
            )

        self.client.post(
            f"/api/v1/interviews/{session_id}/complete/",
            {},
            format="json",
        )

        session.refresh_from_db()
        evaluation = Evaluation.objects.get(session=session)
        self.assertEqual(evaluation.evaluation_tier, InterviewEvaluationTier.FULL)
        self.assertEqual(evaluation.package_code, "advanced")
        self.assertEqual(evaluation.coverage_level, CoverageLevel.FULL)
        self.assertTrue(evaluation.readiness_indicator_enabled)
        self.assertTrue(evaluation.certificate_enabled)
        self.assertEqual(evaluation.status, "COMPLETED")

    def test_screening_package_creates_gated_evaluation_end_to_end(self):
        PackageSessionConfig.objects.create(
            package_code="basic",
            package_name="Basic",
            audience="B2C",
            evaluation_tier=InterviewEvaluationTier.SCREENING,
            min_questions=5,
            max_questions=8,
            default_question_count=8,
            duration_minutes=15,
            task_observation_enabled=False,
            readiness_indicator_enabled=False,
            certificate_enabled=False,
            basic_report_enabled=True,
        )
        RolePackageCoverage.objects.create(
            role_name="Nanny",
            role_code="nanny",
            package_code="basic",
            package_name="Basic",
            audience="B2C",
            coverage_level=CoverageLevel.SCREENING,
            evaluation_tier=InterviewEvaluationTier.SCREENING,
            readiness_indicator_enabled=False,
            certificate_enabled=False,
            video_introduction_enabled=False,
            behavioral_indicators_enabled=False,
        )

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
                "package_code": "basic",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session_id = create_response.data["id"]
        self.client.post(f"/api/v1/interviews/{session_id}/start/", {}, format="json")
        session = InterviewSession.objects.get(public_id=session_id)

        for question in session.questions.order_by("question_order"):
            question.status = "ASKED"
            question.asked_at = timezone.now()
            question.save(update_fields=["status", "asked_at", "updated_at"])
            response = self.client.post(
                f"/api/v1/interviews/{session_id}/submit-response/",
                {
                    "question_id": str(question.public_id),
                    "transcript": f"Answer {question.question_order}",
                    "text_response": f"Answer {question.question_order}",
                },
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.data)

        complete_response = self.client.post(
            f"/api/v1/interviews/{session_id}/complete/",
            {},
            format="json",
        )
        self.assertEqual(complete_response.status_code, 200, complete_response.data)

        evaluation = Evaluation.objects.get(session=session)
        self.assertEqual(evaluation.evaluation_tier, InterviewEvaluationTier.SCREENING)
        self.assertEqual(evaluation.coverage_level, CoverageLevel.SCREENING)
        self.assertFalse(evaluation.readiness_indicator_enabled)
        self.assertFalse(evaluation.certificate_enabled)
        self.assertEqual(evaluation.certificate_status, "NOT_ISSUED")

    def test_current_question_endpoint_returns_active_question(self):
        session = self._create_and_start_session()

        response = self.client.get(f"/api/v1/interviews/{session.public_id}/current-question/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["question_order"], 1)
        self.assertEqual(response.data["status"], "ASKED")
        active_question = session.questions.select_related("question_template").order_by("question_order").first()
        self.assertIsNotNone(active_question)
        self.assertEqual(response.data["domain"], active_question.domain)
        self.assertEqual(response.data["skill_tag"], active_question.question_template.skill_tag)
        self.assertEqual(response.data["skill"], active_question.skill)

    def test_question_generation_avoids_repeating_prior_question_codes_for_candidate(self):
        previous_session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            translation_target="",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1",
            question_set_version="v1",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
            status="COMPLETED",
            started_at=timezone.now() - timezone.timedelta(days=2),
            ended_at=timezone.now() - timezone.timedelta(days=2),
        )
        repeated_template = QuestionTemplate.objects.get(question_code="NAN-001")
        previous_session.questions.create(
            question_template=repeated_template,
            question_text=repeated_template.question_text,
            domain=repeated_template.domain,
            skill=repeated_template.skill,
            difficulty=repeated_template.difficulty,
            question_order=1,
            status="ANSWERED",
            asked_at=timezone.now() - timezone.timedelta(days=2),
            answered_at=timezone.now() - timezone.timedelta(days=2),
        )
        QuestionTemplate.objects.create(
            role_name="Nanny",
            role_code="nanny",
            question_code="NAN-101",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Communication",
            skill_tag="Greeting",
            skill="Greeting",
            sequence_number=10,
            difficulty=QuestionDifficulty.EASY,
            question_text="How do you greet a child in the morning?",
            question_type="communication",
            question_format="TEXT",
            expected_steps=["greet", "smile"],
            keywords=["greet", "morning"],
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=1,
            estimated_time_seconds=30,
            expected_answer_type="structured",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1",
            question_set_version="v1",
        )

        response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        generated_codes = {
            question.question_template.question_code
            for question in InterviewSession.objects.get(public_id=response.data["id"]).questions.select_related("question_template")
        }
        self.assertNotIn("NAN-001", generated_codes)

    def test_question_generation_balances_context_across_domains_and_skills(self):
        QuestionTemplate.objects.all().delete()
        fixtures = [
            ("NAN-A1", "Safety", "Hazard Awareness", "What would you do if the floor is wet?", "safety", QuestionDifficulty.EASY),
            ("NAN-A2", "Safety", "Emergency Response", "What would you do in a kitchen fire?", "safety", QuestionDifficulty.MEDIUM),
            ("NAN-B1", "Care", "Child Comfort", "How do you calm a crying child?", "behavioral", QuestionDifficulty.MEDIUM),
            ("NAN-C1", "Communication", "Employer Update", "How do you report a problem to an employer?", "communication", QuestionDifficulty.HARD),
        ]
        for index, (code, domain, skill, text, qtype, difficulty) in enumerate(fixtures, start=1):
            QuestionTemplate.objects.create(
                role_name="Nanny",
                role_code="nanny",
                question_code=code,
                question_version="1.0",
                question_status=QuestionLifecycleStatus.ACTIVE,
                domain=domain,
                skill_tag=skill,
                skill=skill,
                sequence_number=index,
                difficulty=difficulty,
                question_text=text,
                question_type=qtype,
                question_format="TEXT",
                expected_steps=["step1", "step2"],
                keywords=[domain.lower(), skill.lower()],
                language="EN",
                scoring_type="0/3/5",
                difficulty_score=2,
                estimated_time_seconds=30,
                expected_answer_type="structured",
                evaluation_tier=InterviewEvaluationTier.FULL,
                rubric_version="v1",
                question_set_version="v1",
            )

        response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(self.config.public_id),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        session = InterviewSession.objects.get(public_id=response.data["id"])
        generated = list(session.questions.select_related("question_template").order_by("question_order"))
        domains = {question.domain for question in generated}
        skills = {question.skill for question in generated}
        skill_tags = {question.skill_tag for question in generated}
        self.assertGreaterEqual(len(domains), 3)
        self.assertEqual(len(skills), len(generated))
        self.assertEqual(skill_tags, skills)

    def test_task_observation_flow_assigns_starts_and_completes_task(self):
        task_config = InterviewConfiguration.objects.create(
            role_name="Nanny",
            role_code="nanny",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30,
            total_questions=2,
            allow_retries=True,
            max_retries=1,
            enable_task_module=True,
            rubric_version="v1",
            question_set_version="v1",
        )
        ObservedTaskDefinition.objects.create(
            task_code="NAN-TASK-001",
            task_name="Pick Up Toy",
            role_code="nanny",
            description="Observe basic object handling",
            instruction_text="Pick up the toy, show it, and place it back.",
            expected_steps=["picked_object", "showed_object", "returned_object"],
            max_duration_seconds=20,
            is_active=True,
        )

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(task_config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        session = InterviewSession.objects.get(public_id=create_response.data["id"])
        self.assertTrue(session.task_observation_enabled)
        self.assertEqual(session.observed_tasks.count(), 1)

        self.client.post(f"/api/v1/interviews/{session.public_id}/start/", {}, format="json")

        start_task = self.client.post(f"/api/v1/interviews/{session.public_id}/tasks/start/", {}, format="json")
        self.assertEqual(start_task.status_code, 200, start_task.data)
        self.assertEqual(start_task.data["status"], "IN_PROGRESS")

        task_id = start_task.data["id"]
        complete_task = self.client.post(
            f"/api/v1/interviews/{session.public_id}/tasks/{task_id}/complete/",
            {
                "execution_time_seconds": 10,
                "observed_steps": ["picked_object", "showed_object", "returned_object"],
                "review_required": False,
                "result_payload": {"source": "frontend-camera-flow"},
            },
            format="json",
        )
        self.assertEqual(complete_task.status_code, 200, complete_task.data)
        self.assertEqual(complete_task.data["status"], "COMPLETED")
        self.assertTrue(complete_task.data["task_completed"])
        self.assertTrue(complete_task.data["sequence_correct"])

        session_task = SessionObservedTask.objects.get(public_id=task_id)
        self.assertEqual(session_task.status, "COMPLETED")
        result = TaskObservationResult.objects.get(session_task=session_task)
        self.assertEqual(result.execution_time_seconds, 10)
        self.assertEqual(result.result_payload["source"], "frontend-camera-flow")

        current_task = self.client.get(f"/api/v1/interviews/{session.public_id}/tasks/current/")
        self.assertEqual(current_task.status_code, 200)
        self.assertEqual(current_task.data["status"], "COMPLETED")

        results = self.client.get(f"/api/v1/interviews/{session.public_id}/tasks/results/")
        self.assertEqual(results.status_code, 200)
        self.assertEqual(len(results.data), 1)

        detail = self.client.get(f"/api/v1/interviews/tasks/{result.public_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["id"], str(result.public_id))
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.TASK_OBSERVATION_STARTED).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.TASK_OBSERVATION_COMPLETED).exists())

    def test_task_completion_rejects_duplicate_submission_after_completion(self):
        session = self._create_task_enabled_session()
        self.client.post(f"/api/v1/interviews/{session.public_id}/start/", {}, format="json")

        start_task = self.client.post(f"/api/v1/interviews/{session.public_id}/tasks/start/", {}, format="json")
        task_id = start_task.data["id"]
        first_complete = self.client.post(
            f"/api/v1/interviews/{session.public_id}/tasks/{task_id}/complete/",
            {
                "execution_time_seconds": 10,
                "observed_steps": ["picked_object", "showed_object", "returned_object"],
            },
            format="json",
        )
        self.assertEqual(first_complete.status_code, 200, first_complete.data)

        replay_complete = self.client.post(
            f"/api/v1/interviews/{session.public_id}/tasks/{task_id}/complete/",
            {
                "execution_time_seconds": 10,
                "observed_steps": ["picked_object", "showed_object", "returned_object"],
            },
            format="json",
        )

        self.assertEqual(replay_complete.status_code, 400)
        self.assertIn("finalized", replay_complete.data["detail"].lower())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.TASK_OBSERVATION_INVALID_TRANSITION).exists())

    def test_task_completion_rejects_duplicate_submission_for_failed_terminal_task(self):
        session = self._create_task_enabled_session()
        task = session.observed_tasks.first()
        task.status = "FAILED"
        task.save(update_fields=["status", "updated_at"])
        self.client.post(f"/api/v1/interviews/{session.public_id}/start/", {}, format="json")

        response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/tasks/{task.public_id}/complete/",
            {
                "execution_time_seconds": 6,
                "observed_steps": ["picked_object"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("failed", response.data["detail"].lower())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.TASK_OBSERVATION_INVALID_TRANSITION).exists())

    def test_task_completion_requires_start_and_logs_invalid_transition(self):
        session = self._create_task_enabled_session()

        task = session.observed_tasks.first()
        response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/tasks/{task.public_id}/complete/",
            {
                "execution_time_seconds": 8,
                "observed_steps": ["picked_object"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("started", response.data["detail"].lower())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.TASK_OBSERVATION_INVALID_TRANSITION).exists())

    def test_task_completion_with_session_token_supports_review_flow(self):
        session = self._create_task_enabled_session()
        token_client = APIClient()

        start = token_client.post(
            f"/api/v1/interviews/{session.public_id}/tasks/start/",
            {"token": session.access_token},
            format="json",
            HTTP_X_SESSION_TOKEN=session.access_token,
        )
        self.assertEqual(start.status_code, 200, start.data)

        complete = token_client.post(
            f"/api/v1/interviews/{session.public_id}/tasks/{start.data['id']}/complete/",
            {
                "token": session.access_token,
                "execution_time_seconds": 25,
                "observed_steps": ["picked_object", "returned_object"],
                "integrity_flags": ["camera_occlusion"],
            },
            format="json",
            HTTP_X_SESSION_TOKEN=session.access_token,
        )

        self.assertEqual(complete.status_code, 200, complete.data)
        self.assertTrue(complete.data["review_required"])
        self.assertEqual(complete.data["status"], "INCOMPLETE")
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.TASK_OBSERVATION_REQUIRES_REVIEW).exists())

    def test_unauthorized_user_cannot_access_task_observation_endpoints(self):
        session = self._create_task_enabled_session()
        other_user = User.objects.create_user(
            email="outsider@example.com",
            password="testpass123",
            first_name="Out",
            last_name="Sider",
            role=Roles.B2C,
            is_verified=True,
        )
        self.client.force_authenticate(other_user)

        response = self.client.post(f"/api/v1/interviews/{session.public_id}/tasks/start/", {}, format="json")

        self.assertEqual(response.status_code, 403)

    def test_upload_response_audio_persists_voice_metadata_and_audits(self):
        session = self._create_and_start_session()
        question = session.questions.order_by("question_order").first()
        question.status = "ASKED"
        question.asked_at = timezone.now()
        question.save(update_fields=["status", "asked_at", "updated_at"])

        response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 12,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 200, response.data)
        stored = CandidateResponse.objects.get(public_id=response.data["id"])
        self.assertEqual(stored.response_type, "VOICE")
        self.assertEqual(stored.audio_mime_type, "audio/webm")
        self.assertEqual(stored.audio_file_size_bytes, len(b"voice-bytes"))
        self.assertEqual(stored.stt_status, "PENDING")
        self.assertTrue(stored.audio_file.name)
        self.assertEqual(stored.question.status, "ANSWERED")
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.AUDIO_UPLOAD_COMPLETED).exists())

    def test_upload_response_audio_supports_session_token_access(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        token_client = APIClient()

        response = token_client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 10,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
                "token": session.access_token,
            },
            format="multipart",
            HTTP_X_SESSION_TOKEN=session.access_token,
        )

        self.assertEqual(response.status_code, 200, response.data)
        audit = AuditLog.objects.filter(action=AuditLogAction.AUDIO_UPLOAD_COMPLETED).latest("created_at")
        self.assertEqual(audit.data["access_context"], "session_token")

    def test_voice_endpoints_reject_invalid_session_token(self):
        session = self._create_and_start_session()
        token_client = APIClient()

        response = token_client.get(
            f"/api/v1/interviews/{session.public_id}/current-question/",
            HTTP_X_SESSION_TOKEN="bad-token",
        )

        self.assertEqual(response.status_code, 403)

    def test_upload_response_audio_rejects_invalid_type(self):
        session = self._create_and_start_session()
        question = session.questions.order_by("question_order").first()
        question.status = "ASKED"
        question.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 12,
                "audio_file": SimpleUploadedFile("answer.txt", b"not-audio", content_type="text/plain"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported audio type", str(response.data["detail"]))

    @override_settings(INTERVIEW_AUDIO_MAX_FILE_SIZE_BYTES=4)
    def test_upload_response_audio_rejects_oversized_file(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)

        response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 12,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Audio file is too large", str(response.data["detail"]))

    def test_upload_response_audio_rejects_expired_session(self):
        session = self._create_and_start_session()
        session.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        session.status = "EXPIRED"
        session.save(update_fields=["expires_at", "status", "updated_at"])
        question = session.questions.order_by("question_order").first()
        question.status = "ASKED"
        question.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 12,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Cannot answer after session is closed", str(response.data["detail"]))

    def test_upload_response_audio_rejects_duplicate_submission_for_answered_question(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)

        first = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 12,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
            },
            format="multipart",
        )
        second = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 12,
                "audio_file": SimpleUploadedFile("answer-2.webm", b"voice-bytes-2", content_type="audio/webm"),
            },
            format="multipart",
        )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 400)
        self.assertIn("Question is not currently active", str(second.data["detail"]))

    def test_transcribe_response_success_path(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        upload_response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 9,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
            },
            format="multipart",
        )
        response_id = upload_response.data["id"]

        class FakeSttService:
            provider = "OPENAI"

            def transcribe(self, **kwargs):
                return {
                    "provider": "OPENAI",
                    "provider_model": "whisper-1",
                    "request_id": "req_123",
                    "detected_language": "en",
                    "confidence": None,
                    "processing_status": "COMPLETED",
                    "transcript": "I would keep the child safe first.",
                    "metadata": {"segments": []},
                }

        with patch("api.sessions.services.InterviewVoicePipelineService.stt_service_class", FakeSttService):
            response = self.client.post(
                f"/api/v1/interviews/{session.public_id}/transcribe-response/",
                {"response_id": response_id},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        stored = CandidateResponse.objects.get(public_id=response_id)
        self.assertEqual(stored.transcript, "I would keep the child safe first.")
        self.assertEqual(stored.original_transcript, "I would keep the child safe first.")
        self.assertEqual(stored.stt_status, "COMPLETED")
        self.assertEqual(stored.stt_provider, "OPENAI")
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.TRANSCRIPTION_COMPLETED).exists())

    def test_transcribe_response_passes_candidate_selected_language_as_stt_hint(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        upload_response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 9,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
            },
            format="multipart",
        )
        response_id = upload_response.data["id"]

        received_kwargs = {}

        class FakeSttService:
            provider = "OPENAI"

            def transcribe(self, **kwargs):
                received_kwargs.update(kwargs)
                return {
                    "provider": "OPENAI",
                    "provider_model": "whisper-1",
                    "request_id": "req_123",
                    # Simulate the provider not returning a detected language,
                    # so the stored value must fall back to the requested hint.
                    "detected_language": "",
                    "confidence": None,
                    "processing_status": "COMPLETED",
                    "transcript": "Marhaba, ismi ana.",
                    "metadata": {"segments": []},
                }

        with patch("api.sessions.services.InterviewVoicePipelineService.stt_service_class", FakeSttService):
            response = self.client.post(
                f"/api/v1/interviews/{session.public_id}/transcribe-response/",
                {"response_id": response_id, "language_code": "ar-SA"},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(received_kwargs["language_code"], "ar-SA")
        stored = CandidateResponse.objects.get(public_id=response_id)
        self.assertEqual(stored.transcript_language, "ar-SA")

    def test_transcribe_response_rejects_unsupported_answer_language(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        upload_response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 9,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
            },
            format="multipart",
        )
        response_id = upload_response.data["id"]

        response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/transcribe-response/",
            {"response_id": response_id, "language_code": "de-DE"},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        stored = CandidateResponse.objects.get(public_id=response_id)
        self.assertEqual(stored.stt_status, "PENDING")

    @patch("api.translation.services.AIProcessingOrchestrationService.auto_process_response_ai")
    def test_transcribe_response_triggers_automatic_ai_processing(self, auto_process_mock):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        upload_response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 9,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
            },
            format="multipart",
        )
        response_id = upload_response.data["id"]

        class FakeSttService:
            provider = "OPENAI"

            def transcribe(self, **kwargs):
                return {
                    "provider": "OPENAI",
                    "provider_model": "whisper-1",
                    "request_id": "req_123",
                    "detected_language": "en",
                    "confidence": None,
                    "processing_status": "COMPLETED",
                    "transcript": "I would keep the child safe first.",
                    "metadata": {"segments": []},
                }

        with patch("api.sessions.services.InterviewVoicePipelineService.stt_service_class", FakeSttService):
            response = self.client.post(
                f"/api/v1/interviews/{session.public_id}/transcribe-response/",
                {"response_id": response_id},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        stored = CandidateResponse.objects.get(public_id=response_id)
        auto_process_mock.assert_called_once()
        self.assertEqual(auto_process_mock.call_args.kwargs["response"].id, stored.id)

    def test_transcribe_response_is_retry_safe_after_success(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        upload_response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 9,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
            },
            format="multipart",
        )
        response_id = upload_response.data["id"]

        class FakeSttService:
            provider = "OPENAI"
            calls = 0

            def transcribe(self, **kwargs):
                type(self).calls += 1
                return {
                    "provider": "OPENAI",
                    "provider_model": "whisper-1",
                    "request_id": "req_123",
                    "detected_language": "en",
                    "confidence": None,
                    "processing_status": "COMPLETED",
                    "transcript": "Stable transcript",
                    "metadata": {"segments": []},
                }

        with patch("api.sessions.services.InterviewVoicePipelineService.stt_service_class", FakeSttService):
            first = self.client.post(
                f"/api/v1/interviews/{session.public_id}/transcribe-response/",
                {"response_id": response_id},
                format="json",
            )
            second = self.client.post(
                f"/api/v1/interviews/{session.public_id}/transcribe-response/",
                {"response_id": response_id},
                format="json",
            )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(FakeSttService.calls, 1)

    def test_transcribe_response_failure_path(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        upload_response = self.client.post(
            f"/api/v1/interviews/{session.public_id}/upload-response-audio/",
            {
                "question_id": str(question.public_id),
                "duration_seconds": 9,
                "audio_file": SimpleUploadedFile("answer.webm", b"voice-bytes", content_type="audio/webm"),
            },
            format="multipart",
        )
        response_id = upload_response.data["id"]

        class FakeSttService:
            provider = "OPENAI"

            def transcribe(self, **kwargs):
                raise VoiceProviderError("Speech-to-text provider timed out", code="stt_timeout")

        with patch("api.sessions.services.InterviewVoicePipelineService.stt_service_class", FakeSttService):
            response = self.client.post(
                f"/api/v1/interviews/{session.public_id}/transcribe-response/",
                {"response_id": response_id},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        stored = CandidateResponse.objects.get(public_id=response_id)
        self.assertEqual(stored.stt_status, "FAILED")
        self.assertEqual(stored.stt_error_code, "stt_timeout")
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.TRANSCRIPTION_FAILED).exists())

    @override_settings(
        STT_API_URL="https://api.openai.com/v1/audio/transcriptions",
        STT_API_KEY="test-stt-key",
        STT_MODEL="whisper-1",
    )
    def test_stt_service_retries_without_language_hint_when_provider_rejects_it(self):
        # Whisper's `language` parameter only accepts a fixed enum - some
        # codes this app treats as STT-capable (e.g. Amharic) aren't in it,
        # even though the model can often still transcribe them reasonably
        # via auto-detection. This reproduces the real rejection body OpenAI
        # returns for "am" and confirms the service retries once without the
        # hint instead of failing the whole request.
        from api.interviews.voice_services import SpeechToTextService

        class FakeRejectedResponse:
            status_code = 400

            def json(self):
                return {"error": {"message": "Language 'am' is not supported.", "code": "unsupported_language"}}

            def raise_for_status(self):
                raise requests.HTTPError(response=self)

        class FakeSuccessResponse:
            status_code = 200
            headers = {}

            def json(self):
                return {"text": "hello", "language": "english", "duration": 2.0, "segments": []}

            def raise_for_status(self):
                pass

        responses = [FakeRejectedResponse(), FakeSuccessResponse()]
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(dict(kwargs.get("data", {})))
            return responses.pop(0)

        service = SpeechToTextService()
        with patch("api.interviews.voice_services.requests.post", side_effect=fake_post):
            result = service.transcribe(
                file_obj=SimpleUploadedFile("turn.webm", b"audio-bytes", content_type="audio/webm"),
                filename="turn.webm",
                mime_type="audio/webm",
                language_code="am-ET",
            )

        self.assertEqual(result["transcript"], "hello")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].get("language"), "am")
        self.assertNotIn("language", calls[1])

    @override_settings(
        STT_API_URL="https://api.openai.com/v1/audio/transcriptions",
        STT_API_KEY="test-stt-key",
        STT_MODEL="whisper-1",
    )
    def test_stt_service_does_not_retry_for_other_400_reasons(self):
        from api.interviews.voice_services import SpeechToTextService

        class FakeRejectedResponse:
            status_code = 400

            def json(self):
                return {"error": {"message": "Invalid file format.", "code": "invalid_file"}}

            def raise_for_status(self):
                raise requests.HTTPError(response=self)

        calls = []

        def fake_post(*args, **kwargs):
            calls.append(dict(kwargs.get("data", {})))
            return FakeRejectedResponse()

        service = SpeechToTextService()
        with patch("api.interviews.voice_services.requests.post", side_effect=fake_post):
            with self.assertRaises(VoiceProviderError):
                service.transcribe(
                    file_obj=SimpleUploadedFile("turn.webm", b"audio-bytes", content_type="audio/webm"),
                    filename="turn.webm",
                    mime_type="audio/webm",
                    language_code="am-ET",
                )

        self.assertEqual(len(calls), 1)

    @override_settings(
        STT_API_URL="https://api.openai.com/v1/audio/transcriptions",
        STT_API_KEY="test-stt-key",
        STT_MODEL="whisper-1",
        AZURE_SPEECH_KEY="test-azure-key",
        AZURE_SPEECH_REGION="test-region",
        STT_AZURE_LANGUAGES=["am"],
    )
    def test_stt_service_routes_configured_language_to_azure(self):
        # Whisper can't transcribe Amharic at all (see WHISPER_DETECTED_LANGUAGE_NAMES) -
        # confirmed languages route to Azure Speech instead. Transcoding
        # itself (webm -> WAV) is exercised separately below; here we're
        # confirming the routing decision and response-shape translation.
        from api.interviews.voice_services import SpeechToTextService

        class FakeAzureResponse:
            status_code = 200
            headers = {"x-requestid": "azure-req-1"}

            def json(self):
                return {
                    "RecognitionStatus": "Success",
                    "DisplayText": "hello there",
                    "NBest": [{"Confidence": 0.84}],
                }

            def raise_for_status(self):
                pass

        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return FakeAzureResponse()

        service = SpeechToTextService()
        with patch.object(SpeechToTextService, "_transcode_to_wav", return_value=b"wav-bytes"):
            with patch("api.interviews.voice_services.requests.post", side_effect=fake_post):
                result = service.transcribe(
                    file_obj=SimpleUploadedFile("turn.webm", b"audio-bytes", content_type="audio/webm"),
                    filename="turn.webm",
                    mime_type="audio/webm",
                    language_code="am-ET",
                )

        self.assertEqual(result["provider"], "AZURE")
        self.assertEqual(result["transcript"], "hello there")
        self.assertEqual(result["detected_language"], "am-ET")
        self.assertEqual(result["confidence"], 0.84)
        self.assertEqual(service.provider, "AZURE")
        self.assertEqual(len(calls), 1)
        self.assertIn("stt.speech.microsoft.com", calls[0])

    @override_settings(
        STT_API_URL="https://api.openai.com/v1/audio/transcriptions",
        STT_API_KEY="test-stt-key",
        STT_MODEL="whisper-1",
        AZURE_SPEECH_KEY="test-azure-key",
        AZURE_SPEECH_REGION="test-region",
        STT_AZURE_LANGUAGES=["am"],
    )
    def test_stt_service_does_not_route_unconfigured_language_to_azure(self):
        # A language not in STT_AZURE_LANGUAGES (e.g. English, which Whisper
        # already handles correctly) must keep going through Whisper -
        # regression guard against the routing change affecting anything
        # that already worked.
        from api.interviews.voice_services import SpeechToTextService

        class FakeWhisperResponse:
            status_code = 200
            headers = {}

            def json(self):
                return {"text": "hello there", "language": "english", "segments": []}

            def raise_for_status(self):
                pass

        service = SpeechToTextService()
        with patch.object(SpeechToTextService, "_transcode_to_wav") as fake_transcode:
            with patch("api.interviews.voice_services.requests.post", return_value=FakeWhisperResponse()):
                result = service.transcribe(
                    file_obj=SimpleUploadedFile("turn.webm", b"audio-bytes", content_type="audio/webm"),
                    filename="turn.webm",
                    mime_type="audio/webm",
                    language_code="en-US",
                )

        self.assertEqual(result["provider"], "OPENAI")
        fake_transcode.assert_not_called()

    @override_settings(AZURE_SPEECH_KEY="test-azure-key", AZURE_SPEECH_REGION="test-region", STT_AZURE_LANGUAGES=["am"])
    def test_stt_service_azure_transcode_failure_raises_voice_provider_error(self):
        from api.interviews.voice_services import SpeechToTextService

        service = SpeechToTextService()
        with patch.object(
            SpeechToTextService,
            "_transcode_to_wav",
            side_effect=VoiceProviderError("Audio conversion failed", code="stt_transcode_failed"),
        ):
            with self.assertRaises(VoiceProviderError) as ctx:
                service.transcribe(
                    file_obj=SimpleUploadedFile("turn.webm", b"audio-bytes", content_type="audio/webm"),
                    filename="turn.webm",
                    mime_type="audio/webm",
                    language_code="am-ET",
                )
        self.assertEqual(ctx.exception.code, "stt_transcode_failed")

    def test_transcode_to_wav_wraps_ffmpeg_failure(self):
        # Exercises the real _transcode_to_wav implementation (not mocked)
        # against a genuinely invalid input, confirming subprocess/file
        # errors surface as VoiceProviderError rather than an unhandled
        # exception - independent of whether ffmpeg itself is installed
        # here (FileNotFoundError is caught the same way).
        from api.interviews.voice_services import SpeechToTextService

        with self.assertRaises(VoiceProviderError) as ctx:
            SpeechToTextService._transcode_to_wav(b"not-real-audio-bytes")
        self.assertEqual(ctx.exception.code, "stt_transcode_failed")

    def test_question_audio_generation_and_caching(self):
        session = self._create_and_start_session()

        class FakeTtsService:
            def __init__(self):
                self.provider = "GOOGLE"

            def synthesize(self, **kwargs):
                return {
                    "provider": "GOOGLE",
                    "voice_name": "en-US-Standard-C",
                    "language_code": "en-US",
                    "mime_type": "audio/mpeg",
                    "audio_bytes": b"mp3-bytes",
                    "duration_estimate_seconds": 4,
                    "metadata": {"audio_encoding": "MP3"},
                }

        with patch("api.sessions.services.InterviewVoicePipelineService.tts_service_class", FakeTtsService):
            first = self.client.post(f"/api/v1/interviews/{session.public_id}/question-audio/", {}, format="json")
            second = self.client.post(f"/api/v1/interviews/{session.public_id}/question-audio/", {}, format="json")

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.QUESTION_AUDIO_GENERATED).exists())

    def test_question_audio_failure_returns_controlled_error(self):
        session = self._create_and_start_session()

        class FakeTtsService:
            def __init__(self):
                self.provider = "GOOGLE"

            def synthesize(self, **kwargs):
                raise VoiceProviderError("Text-to-speech provider timed out", code="tts_timeout")

        with patch("api.sessions.services.InterviewVoicePipelineService.tts_service_class", FakeTtsService):
            response = self.client.post(f"/api/v1/interviews/{session.public_id}/question-audio/", {}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Text-to-speech provider timed out", str(response.data["detail"]))
        self.assertFalse(session.question_audio_artifacts.exists())

    def test_process_ai_runs_translation_interpretation_and_rule_input_end_to_end(self):
        session = self._create_and_start_session()
        session.translation_target = "EN"
        session.save(update_fields=["translation_target", "updated_at"])
        question = self._mark_first_question_asked(session)
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type="TEXT",
            transcript="ابق الطفل بعيدا ثم نظف السائل",
            original_transcript="ابق الطفل بعيدا ثم نظف السائل",
            transcript_language="ar",
            stt_status="COMPLETED",
        )

        class FakeTranslationProvider:
            def __init__(self):
                self.provider = "GOOGLE"

            def translate(self, **kwargs):
                return {
                    "provider": "GOOGLE",
                    "provider_model": "google-translate-v2",
                    "translated_text": "Keep the child away, then clean the spill.",
                    "source_language": "ar",
                    "target_language": "en",
                    "metadata": {"request_id": "translate-123"},
                }

        class FakeInterpretationProvider:
            def interpret(self, **kwargs):
                return {
                    "provider": "OPENAI",
                    "model": "gpt-4o-mini",
                    "raw_content": json.dumps(
                        {
                            "answer_relevance": "high",
                            # "step1" is the fixture's canonical expected_steps
                            # phrasing (see setUp); "kept the child away" is a
                            # paraphrase that should get filtered out by the
                            # closed-vocabulary constraint rather than stored.
                            "mentioned_steps": ["step1", "kept the child away"],
                            "missing_steps": ["step2"],
                            "safety_risks": [],
                            "compliance_risks": [],
                            "language_quality": "clear",
                            "confidence_notes": ["direct answer"],
                            "uncertainty_notes": [],
                            "transcript_issues": [],
                            "key_evidence_phrases": ["keep the child away"],
                        }
                    ),
                    "metadata": {"request_id": "interpret-123"},
                }

        with patch("api.translation.services.TranslationService.provider_class", FakeTranslationProvider), patch(
            "api.translation.services.ResponseInterpretationService.get_provider",
            return_value=FakeInterpretationProvider(),
        ):
            api_response = self.client.post(
                f"/api/v1/interviews/responses/{response.public_id}/process-ai/",
                {},
                format="json",
            )

        self.assertEqual(api_response.status_code, 200, api_response.data)
        stored = CandidateResponse.objects.get(pk=response.pk)
        self.assertEqual(stored.translation_status, "COMPLETED")
        self.assertEqual(stored.translated_transcript, "Keep the child away, then clean the spill.")
        self.assertEqual(stored.original_transcript, "ابق الطفل بعيدا ثم نظف السائل")
        self.assertEqual(stored.interpretation_status, "COMPLETED")
        self.assertEqual(stored.processing_status, "PROCESSING_COMPLETED")
        self.assertTrue(CandidateResponseTranslation.objects.filter(response=stored, status="COMPLETED").exists())
        self.assertTrue(CandidateResponseInterpretation.objects.filter(response=stored, status="COMPLETED").exists())
        interpretation = CandidateResponseInterpretation.objects.get(response=stored)
        self.assertEqual(
            interpretation.normalized_indicators.get("unmatched_step_phrases"), ["kept the child away"]
        )
        artifact = EvaluationInputArtifact.objects.get(response=stored)
        self.assertEqual(artifact.observed_indicators, ["step1"])
        self.assertEqual(artifact.missing_indicators, ["step2"])
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.TRANSLATION_COMPLETED).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.INTERPRETATION_COMPLETED).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.RULE_INPUT_PREPARATION_COMPLETED).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.AI_PROCESSING_COMPLETED).exists())

    def test_process_ai_survives_translation_failure_and_flags_for_review(self):
        session = self._create_and_start_session()
        session.translation_target = "EN"
        session.save(update_fields=["translation_target", "updated_at"])
        question = self._mark_first_question_asked(session)
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type="TEXT",
            transcript="ابق الطفل بعيدا ثم نظف السائل",
            original_transcript="ابق الطفل بعيدا ثم نظف السائل",
            transcript_language="ar",
            stt_status="COMPLETED",
        )

        class FailingTranslationProvider:
            def __init__(self):
                self.provider = "GOOGLE"

            def translate(self, **kwargs):
                raise AIProcessingError("Translation provider timed out", code="translation_timeout")

        class FakeInterpretationProvider:
            def interpret(self, **kwargs):
                return {
                    "provider": "OPENAI",
                    "model": "gpt-4o-mini",
                    "raw_content": json.dumps(
                        {
                            "answer_relevance": "high",
                            "mentioned_steps": ["step1", "step2"],
                            "missing_steps": [],
                            "safety_risks": [],
                            "compliance_risks": [],
                            "language_quality": "clear",
                            "confidence_notes": [],
                            "uncertainty_notes": [],
                            "transcript_issues": [],
                            "key_evidence_phrases": [],
                        }
                    ),
                    "metadata": {},
                }

        with patch(
            "api.translation.services.TranslationService.provider_class", FailingTranslationProvider
        ), patch(
            "api.translation.services.ResponseInterpretationService.get_provider",
            return_value=FakeInterpretationProvider(),
        ):
            api_response = self.client.post(
                f"/api/v1/interviews/responses/{response.public_id}/process-ai/",
                {},
                format="json",
            )

        self.assertEqual(api_response.status_code, 200, api_response.data)
        stored = CandidateResponse.objects.get(pk=response.pk)
        self.assertEqual(stored.translation_status, "FAILED")
        self.assertEqual(stored.interpretation_status, "COMPLETED")
        self.assertEqual(stored.processing_status, "PROCESSING_COMPLETED")
        interpretation = CandidateResponseInterpretation.objects.get(response=stored)
        self.assertEqual(interpretation.input_transcript_type, "original_untranslated")
        self.assertEqual(interpretation.input_language, "ar")
        artifact = EvaluationInputArtifact.objects.get(response=stored)
        self.assertEqual(artifact.observed_indicators, ["step1", "step2"])
        self.assertTrue(artifact.requires_human_review)
        self.assertIn("Translation failed", artifact.review_reason)

    def test_resolve_interpretation_input_falls_back_to_original_on_translation_failure(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type="TEXT",
            transcript="Texto original en espanol.",
            original_transcript="Texto original en espanol.",
            transcript_language="es",
            translation_status="FAILED",
            stt_status="COMPLETED",
        )

        transcript, language, transcript_type = AIProcessingOrchestrationService._resolve_interpretation_input(
            response
        )

        self.assertEqual(transcript, "Texto original en espanol.")
        self.assertEqual(language, "es")
        self.assertEqual(transcript_type, "original_untranslated")

    def test_resolve_interpretation_input_uses_translation_when_completed(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type="TEXT",
            transcript="Texto original en espanol.",
            original_transcript="Texto original en espanol.",
            transcript_language="es",
            translation_status="COMPLETED",
            translated_transcript="Original text in Spanish.",
            translation_target_language="en",
            stt_status="COMPLETED",
        )

        transcript, language, transcript_type = AIProcessingOrchestrationService._resolve_interpretation_input(
            response
        )

        self.assertEqual(transcript, "Original text in Spanish.")
        self.assertEqual(language, "en")
        self.assertEqual(transcript_type, "translated")

    def test_interpretation_step_extraction_without_canonical_steps_is_unfiltered(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        question.question_template.expected_steps = []
        question.question_template.save(update_fields=["expected_steps"])
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type="TEXT",
            transcript="I would clean the spill and warn others.",
            original_transcript="I would clean the spill and warn others.",
            transcript_language="en",
            translation_status="NOT_REQUIRED",
            stt_status="COMPLETED",
        )

        class FakeInterpretationProvider:
            def interpret(self, **kwargs):
                return {
                    "provider": "OPENAI",
                    "model": "gpt-4o-mini",
                    "raw_content": json.dumps(
                        {
                            "answer_relevance": "high",
                            "mentioned_steps": ["cleaned the spill", "warned others"],
                            "missing_steps": [],
                            "safety_risks": [],
                            "compliance_risks": [],
                            "language_quality": "clear",
                            "confidence_notes": [],
                            "uncertainty_notes": [],
                            "transcript_issues": [],
                            "key_evidence_phrases": [],
                        }
                    ),
                    "metadata": {},
                }

        with patch(
            "api.translation.services.ResponseInterpretationService.get_provider",
            return_value=FakeInterpretationProvider(),
        ):
            api_response = self.client.post(
                f"/api/v1/interviews/responses/{response.public_id}/process-ai/",
                {},
                format="json",
            )

        self.assertEqual(api_response.status_code, 200, api_response.data)
        artifact = EvaluationInputArtifact.objects.get(response__pk=response.pk)
        self.assertEqual(artifact.observed_indicators, ["cleaned the spill", "warned others"])

    def test_process_ai_skips_translation_when_languages_match(self):
        session = self._create_and_start_session()
        session.translation_target = "EN"
        session.save(update_fields=["translation_target", "updated_at"])
        question = self._mark_first_question_asked(session)
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type="TEXT",
            transcript="I would secure the child and clean the spill.",
            original_transcript="I would secure the child and clean the spill.",
            transcript_language="en",
            stt_status="COMPLETED",
        )
        token_client = APIClient()

        class FakeInterpretationProvider:
            def interpret(self, **kwargs):
                return {
                    "provider": "OPENAI",
                    "model": "gpt-4o-mini",
                    "raw_content": json.dumps(
                        {
                            "answer_relevance": "high",
                            "mentioned_steps": ["secure child", "clean spill"],
                            "missing_steps": [],
                            "safety_risks": [],
                            "compliance_risks": [],
                            "language_quality": "clear",
                            "confidence_notes": [],
                            "uncertainty_notes": [],
                            "transcript_issues": [],
                            "key_evidence_phrases": ["secure the child"],
                        }
                    ),
                    "metadata": {},
                }

        with patch(
            "api.translation.services.ResponseInterpretationService.get_provider",
            return_value=FakeInterpretationProvider(),
        ):
            api_response = token_client.post(
                f"/api/v1/interviews/responses/{response.public_id}/process-ai/",
                {"token": session.access_token},
                format="json",
                HTTP_X_SESSION_TOKEN=session.access_token,
            )
            status_response = token_client.get(
                f"/api/v1/interviews/responses/{response.public_id}/ai-processing-status/",
                {"token": session.access_token},
                HTTP_X_SESSION_TOKEN=session.access_token,
            )

        self.assertEqual(api_response.status_code, 200, api_response.data)
        self.assertEqual(status_response.status_code, 200, status_response.data)
        stored = CandidateResponse.objects.get(pk=response.pk)
        self.assertEqual(stored.translation_status, "NOT_REQUIRED")
        self.assertEqual(stored.translated_transcript, "")
        self.assertEqual(status_response.data["translation_status"], "NOT_REQUIRED")
        self.assertEqual(status_response.data["processing_status"], "PROCESSING_COMPLETED")

    def test_interpret_rejects_prohibited_scoring_fields(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type="TEXT",
            transcript="I would clean the spill.",
            original_transcript="I would clean the spill.",
            transcript_language="en",
            translation_status="NOT_REQUIRED",
            stt_status="COMPLETED",
        )

        class FakeInterpretationProvider:
            def interpret(self, **kwargs):
                return {
                    "provider": "OPENAI",
                    "model": "gpt-4o-mini",
                    "raw_content": json.dumps(
                        {
                            "answer_relevance": "medium",
                            "mentioned_steps": ["clean spill"],
                            "final_score": 95,
                        }
                    ),
                    "metadata": {},
                }

        with patch(
            "api.translation.services.ResponseInterpretationService.get_provider",
            return_value=FakeInterpretationProvider(),
        ):
            api_response = self.client.post(
                f"/api/v1/interviews/responses/{response.public_id}/interpret/",
                {},
                format="json",
            )

        self.assertEqual(api_response.status_code, 200, api_response.data)
        stored = CandidateResponse.objects.get(pk=response.pk)
        self.assertEqual(stored.interpretation_status, "FAILED")
        self.assertIn("prohibited", stored.interpretation_error.lower())
        self.assertFalse(EvaluationInputArtifact.objects.filter(response=stored).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.INTERPRETATION_FAILED).exists())

    @override_settings(AI_INTERPRETATION_MIN_CONFIDENCE=0.75)
    def test_process_ai_flags_low_confidence_for_human_review(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type="TEXT",
            transcript="I think maybe I would clean it.",
            original_transcript="I think maybe I would clean it.",
            transcript_language="en",
            translation_status="NOT_REQUIRED",
            stt_status="COMPLETED",
        )

        class FakeInterpretationProvider:
            def interpret(self, **kwargs):
                return {
                    "provider": "OPENAI",
                    "model": "gpt-4o-mini",
                    "raw_content": json.dumps(
                        {
                            "answer_relevance": "medium",
                            "mentioned_steps": ["clean spill"],
                            "missing_steps": ["protect child"],
                            "safety_risks": [],
                            "compliance_risks": [],
                            "language_quality": "unclear",
                            "extraction_confidence": 0.62,
                            "confidence_notes": [],
                            "uncertainty_notes": ["candidate sounded unsure"],
                            "transcript_issues": [],
                            "key_evidence_phrases": ["I think maybe"],
                        }
                    ),
                    "metadata": {},
                }

        with patch(
            "api.translation.services.ResponseInterpretationService.get_provider",
            return_value=FakeInterpretationProvider(),
        ):
            api_response = self.client.post(
                f"/api/v1/interviews/responses/{response.public_id}/process-ai/",
                {},
                format="json",
            )

        self.assertEqual(api_response.status_code, 200, api_response.data)
        stored = CandidateResponse.objects.get(pk=response.pk)
        artifact = EvaluationInputArtifact.objects.get(response=stored)
        interpretation = CandidateResponseInterpretation.objects.get(response=stored)
        self.assertEqual(stored.processing_status, "PROCESSING_COMPLETED")
        self.assertEqual(str(interpretation.confidence_score), "0.620")
        self.assertTrue(artifact.requires_human_review)
        self.assertIn("below the configured threshold", artifact.review_reason)
        self.assertEqual(
            artifact.legal_disclaimer,
            "AI outputs are advisory extraction artifacts only and never constitute employment decisions.",
        )
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.AI_PROCESSING_REQUIRES_HUMAN_REVIEW).exists())

    @override_settings(ENABLE_ASYNC_AI_PROCESSING=True, AZURE_QUEUE_CONNECTION_STRING="UseDevelopmentStorage=true")
    def test_process_ai_can_queue_async_job_with_idempotency_key(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type="TEXT",
            transcript="I would secure the area and clean it.",
            original_transcript="I would secure the area and clean it.",
            transcript_language="en",
            translation_status="NOT_REQUIRED",
            stt_status="COMPLETED",
        )

        with patch("api.translation.services.enqueue_background_job") as enqueue_mock:
            enqueue_mock.return_value = {
                "queued": True,
                "queue": "meritlense-jobs",
                "message_id": "msg-123",
            }
            api_response = self.client.post(
                f"/api/v1/interviews/responses/{response.public_id}/process-ai/",
                {
                    "async_execution": True,
                    "idempotency_key": "resp-queue-1",
                },
                format="json",
            )
            status_response = self.client.get(
                f"/api/v1/interviews/responses/{response.public_id}/ai-processing-status/"
            )

        self.assertEqual(api_response.status_code, 200, api_response.data)
        self.assertEqual(status_response.status_code, 200, status_response.data)
        stored = CandidateResponse.objects.get(pk=response.pk)
        self.assertEqual(stored.processing_status, "QUEUED")
        self.assertEqual(stored.ai_processing_idempotency_key, "resp-queue-1")
        self.assertEqual(status_response.data["async_job"]["status"], "QUEUED")
        self.assertEqual(status_response.data["async_job"]["idempotency_key"], "resp-queue-1")
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.AI_PROCESSING_QUEUED).exists())

    @override_settings(ENABLE_ASYNC_AI_PROCESSING=True, AZURE_QUEUE_CONNECTION_STRING="UseDevelopmentStorage=true")
    def test_process_ai_job_command_processes_queued_job(self):
        session = self._create_and_start_session()
        question = self._mark_first_question_asked(session)
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type="TEXT",
            transcript="I would first move the child away and then clean the spill.",
            original_transcript="I would first move the child away and then clean the spill.",
            transcript_language="en",
            translation_status="NOT_REQUIRED",
            stt_status="COMPLETED",
        )

        class FakeInterpretationProvider:
            def interpret(self, **kwargs):
                return {
                    "provider": "OPENAI",
                    "model": "gpt-4o-mini",
                    "raw_content": json.dumps(
                        {
                            "answer_relevance": "high",
                            "mentioned_steps": ["move child away", "clean spill"],
                            "missing_steps": [],
                            "safety_risks": [],
                            "compliance_risks": [],
                            "language_quality": "clear",
                            "extraction_confidence": 0.91,
                            "confidence_notes": [],
                            "uncertainty_notes": [],
                            "transcript_issues": [],
                            "key_evidence_phrases": ["move the child away"],
                        }
                    ),
                    "metadata": {},
                }

        with patch("api.translation.services.enqueue_background_job") as enqueue_mock:
            enqueue_mock.return_value = {
                "queued": True,
                "queue": "meritlense-jobs",
                "message_id": "msg-456",
            }
            self.client.post(
                f"/api/v1/interviews/responses/{response.public_id}/process-ai/",
                {"async_execution": True, "idempotency_key": "resp-job-1"},
                format="json",
            )

        job = response.ai_processing_jobs.get(idempotency_key="resp-job-1")
        with patch(
            "api.translation.services.ResponseInterpretationService.get_provider",
            return_value=FakeInterpretationProvider(),
        ):
            call_command("process_ai_job", job_id=str(job.public_id))

        stored = CandidateResponse.objects.get(pk=response.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "COMPLETED")
        self.assertEqual(stored.processing_status, "PROCESSING_COMPLETED")
        self.assertTrue(EvaluationInputArtifact.objects.filter(response=stored).exists())

    def test_cannot_start_expired_session(self):
        session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            translation_target="",
            total_questions=0,
            expires_at=timezone.now() - timezone.timedelta(minutes=5),
            created_by=self.user,
        )
        response = self.client.post(f"/api/v1/interviews/{session.public_id}/start/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Cannot start expired session", str(response.data["detail"]))

    def test_session_generation_accepts_both_tier_questions(self):
        both_config = InterviewConfiguration.objects.create(
            role_name="Housekeeper",
            role_code="domestic_worker",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.SCREENING,
            duration_minutes=20,
            total_questions=1,
            allow_retries=True,
            max_retries=1,
            rubric_version="v2.0",
            question_set_version="v1.2",
        )
        QuestionTemplate.objects.create(
            role_name="Housekeeper",
            role_code="domestic_worker",
            question_code="HK-PRO-999",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Professional Skills",
            skill_tag="Psych & Professional",
            skill="Psych & Professional",
            sequence_number=1,
            difficulty=QuestionDifficulty.EASY,
            question_text="How do you greet your employer?",
            question_type="communication",
            question_format="TEXT",
            language="EN",
            scoring_type="0/2/3",
            difficulty_score=1,
            estimated_time_seconds=60,
            expected_answer_type="short",
            evaluation_tier=InterviewEvaluationTier.BOTH,
            rubric_version="v2.0",
            question_set_version="v1.2",
            is_active=True,
        )

        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(both_config.public_id),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.data["total_questions"], 1)
        self.assertEqual(create_response.data["questions"][0]["question_text"], "How do you greet your employer?")

    def _create_and_start_session(self):
        session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            translation_target="",
            total_questions=3,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1",
            question_set_version="v1",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
            status="IN_PROGRESS",
            started_at=timezone.now(),
        )
        for index, template in enumerate(QuestionTemplate.objects.order_by("sequence_number")[:3], start=1):
            session.questions.create(
                question_template=template,
                question_text=template.question_text,
                domain=template.domain,
                skill=template.skill,
                difficulty=template.difficulty,
                question_order=index,
                is_mandatory=template.is_mandatory,
            )
        return session

    def _create_task_enabled_session(self):
        ObservedTaskDefinition.objects.create(
            task_code="NAN-TASK-001",
            task_name="Pick Up Toy",
            role_code="nanny",
            description="Observe basic object handling",
            instruction_text="Pick up the toy, show it, and place it back.",
            expected_steps=["picked_object", "showed_object", "returned_object"],
            max_duration_seconds=20,
            is_active=True,
        )
        task_config = InterviewConfiguration.objects.create(
            role_name="Nanny",
            role_code="nanny",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30,
            total_questions=2,
            allow_retries=True,
            max_retries=1,
            enable_task_module=True,
            rubric_version="v1",
            question_set_version="v1",
        )
        create_response = self.client.post(
            "/api/v1/interviews/",
            {
                "candidate_id": str(self.candidate.public_id),
                "config_id": str(task_config.public_id),
            },
            format="json",
        )
        session = InterviewSession.objects.get(public_id=create_response.data["id"])
        self.client.post(f"/api/v1/interviews/{session.public_id}/start/", {}, format="json")
        session.refresh_from_db()
        return session

    def _mark_first_question_asked(self, session):
        question = session.questions.order_by("question_order").first()
        question.status = "ASKED"
        question.asked_at = timezone.now()
        question.save(update_fields=["status", "asked_at", "updated_at"])
        session.current_question_index = question.question_order
        session.save(update_fields=["current_question_index", "updated_at"])
        return question


class InterviewSessionWebSocketTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(
            email="socket-owner@example.com",
            password="testpass123",
            first_name="Socket",
            last_name="Owner",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Abel",
            last_name="Socket",
            email="socket-candidate@example.com",
            passport_id="PASS-W3-WS-001",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
            passport_document=make_file("socket-passport.pdf"),
            created_by=self.user,
        )
        PackageBalance.objects.create(
            owner_user=self.user,
            balance_type=PackageBalance.SLOTS,
            fixed_amount=1000,
            current_balance=1000,
        )
        self.config = InterviewConfiguration.objects.create(
            role_name="Nanny",
            role_code="nanny",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30,
            total_questions=1,
            allow_retries=True,
            max_retries=1,
            rubric_version="v1",
            question_set_version="v1",
        )
        QuestionTemplate.objects.create(
            role_name="Nanny",
            role_code="nanny",
            question_code="NAN-WS-001",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Safety",
            skill_tag="Awareness",
            skill="Awareness",
            sequence_number=1,
            difficulty=QuestionDifficulty.EASY,
            question_text="What should you check first?",
            question_type="knowledge",
            question_format="TEXT",
            expected_steps=["check"],
            keywords=["safe"],
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=1,
            estimated_time_seconds=30,
            expected_answer_type="short",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1",
            question_set_version="v1",
        )
        self.session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            translation_target="",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1",
            question_set_version="v1",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
        )

    def test_websocket_connects_and_replies_to_ping(self):
        asyncio.run(self._exercise_socket())

    async def _exercise_socket(self):
        communicator = ApplicationCommunicator(
            application,
            {
                "type": "websocket",
                "path": f"/ws/interview/{self.session.public_id}/",
                "query_string": f"token={self.session.access_token}".encode(),
                "headers": [],
            },
        )

        await communicator.send_input({"type": "websocket.connect"})
        accept = await communicator.receive_output(timeout=1)
        self.assertEqual(accept["type"], "websocket.accept")

        initial_state = await communicator.receive_output(timeout=1)
        initial_payload = json.loads(initial_state["text"])
        self.assertEqual(initial_payload["event"], "SESSION_STATE")

        await communicator.send_input({"type": "websocket.receive", "text": json.dumps({"action": "ping"})})
        pong = await communicator.receive_output(timeout=1)
        pong_payload = json.loads(pong["text"])
        self.assertEqual(pong_payload["event"], "PONG")

        await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait(timeout=1)
