import asyncio
import json
import shutil
import tempfile
from unittest.mock import patch

from asgiref.testing import ApplicationCommunicator
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
from api.questions.models import QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession, ObservedTaskDefinition, SessionObservedTask, TaskObservationResult
from api.translation.models import CandidateResponseInterpretation, CandidateResponseTranslation, EvaluationInputArtifact
from api.evaluations.models import Evaluation
from meritlense.asgi import application


def make_file(name="passport.pdf", content=b"passport"):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


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
        self.assertGreaterEqual(len(domains), 3)
        self.assertEqual(len(skills), len(generated))

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
                            "mentioned_steps": ["keep child away", "clean spill"],
                            "missing_steps": ["prevent recurrence"],
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
        artifact = EvaluationInputArtifact.objects.get(response=stored)
        self.assertEqual(artifact.observed_indicators, ["keep child away", "clean spill"])
        self.assertEqual(artifact.missing_indicators, ["prevent recurrence"])
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.TRANSLATION_COMPLETED).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.INTERPRETATION_COMPLETED).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.RULE_INPUT_PREPARATION_COMPLETED).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.AI_PROCESSING_COMPLETED).exists())

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
