import asyncio
import json

from asgiref.testing import ApplicationCommunicator
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from api.accounts.models import User
from api.candidates.models import Candidate
from api.core.constants import QuestionDifficulty, Roles
from api.interviews.models import InterviewConfiguration
from api.questions.models import QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession
from meritlense.asgi import application


def make_file(name="passport.pdf", content=b"passport"):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


class InterviewSessionApiTests(APITestCase):
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
            language="EN",
            duration_minutes=30,
            total_questions=3,
            allow_retries=True,
            max_retries=1,
        )
        for index in range(1, 4):
            QuestionTemplate.objects.create(
                role_name="Nanny",
                domain="Child Care",
                skill=f"Skill {index}",
                difficulty=QuestionDifficulty.MEDIUM,
                question_text=f"Question text {index}",
                expected_steps=["step1", "step2"],
                keywords=["care", "safe"],
                language="EN",
            )

    def test_can_crud_interview_configuration(self):
        response = self.client.post(
            "/api/v1/interviews/configs/",
            {
                "role_name": "Driver",
                "language": "EN",
                "duration_minutes": 25,
                "total_questions": 2,
                "allow_retries": True,
                "max_retries": 1,
                "enable_translation": False,
                "enable_task_module": False,
                "enable_integrity_checks": False,
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
                "domain": "Care",
                "skill": "Patience",
                "difficulty": "MEDIUM",
                "question_text": "How would you calm a crying child?",
                "expected_steps": ["stay calm", "reassure"],
                "keywords": ["patience", "calm"],
                "weight": "1.00",
                "language": "EN",
                "is_mandatory": True,
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
                "language": "EN",
                "duration_minutes": 25,
                "total_questions": 2,
                "allow_retries": False,
                "max_retries": 0,
                "enable_translation": False,
                "enable_task_module": False,
                "enable_integrity_checks": False,
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
            language="EN",
            duration_minutes=30,
            total_questions=1,
            allow_retries=True,
            max_retries=1,
        )
        QuestionTemplate.objects.create(
            role_name="Nanny",
            domain="Safety",
            skill="Awareness",
            difficulty=QuestionDifficulty.EASY,
            question_text="What should you check first?",
            expected_steps=["check"],
            keywords=["safe"],
            language="EN",
        )
        self.session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            translation_target="",
            total_questions=1,
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
