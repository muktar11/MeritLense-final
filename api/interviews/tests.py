import asyncio
import json
import shutil
import tempfile
from unittest.mock import patch

from asgiref.testing import ApplicationCommunicator
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from api.accounts.models import User
from api.audit.models import AuditLog
from api.candidates.models import Candidate
from api.core.constants import AuditLogAction, InterviewEvaluationTier, QuestionDifficulty, QuestionLifecycleStatus, Roles
from api.interviews.models import InterviewConfiguration, InterviewRubric
from api.interviews.voice_services import VoiceProviderError
from api.questions.models import QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession
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

    def test_current_question_endpoint_returns_active_question(self):
        session = self._create_and_start_session()

        response = self.client.get(f"/api/v1/interviews/{session.public_id}/current-question/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["question_order"], 1)
        self.assertEqual(response.data["status"], "ASKED")

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
