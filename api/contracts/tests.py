import shutil
import tempfile

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from api.accounts.models import User
from api.candidates.models import Candidate
from api.core.constants import InterviewEvaluationTier, Languages, QuestionDifficulty, QuestionLifecycleStatus, Roles, candidateJobRoles
from api.interviews.models import InterviewConfiguration
from api.questions.models import QuestionTemplate
from api.sessions.models import InterviewSession

from .models import Agreement, AgreementType, CookieConsent


def make_file(name="test.pdf", content=b"pdf-content", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class ContractApiTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_dir = tempfile.mkdtemp(prefix="contracts-tests-")
        cls._override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            email="b2c@example.com",
            password="Password123!",
            first_name="B2C",
            last_name="Owner",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Selam",
            last_name="Candidate",
            email="candidate@example.com",
            passport_id="PASS-CONTRACT-1",
            job_role=candidateJobRoles.NANNY,
            core_skills="communication, patience",
            preferred_language=Languages.ENGLISH,
            passport_document=make_file("passport.pdf"),
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
            question_code="NAN-001",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Child Care",
            skill_tag="Safety",
            skill="Safety",
            sequence_number=1,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text="How would you keep a child safe?",
            question_type="knowledge",
            question_format="TEXT",
            expected_steps=["monitor", "respond"],
            keywords=["safe"],
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=2,
            estimated_time_seconds=60,
            expected_answer_type="structured",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1",
            question_set_version="v1",
        )
        self.session = InterviewSession.objects.create(
            candidate=self.candidate,
            config=self.config,
            role_name="Nanny",
            role_code="nanny",
            total_questions=1,
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
        )

    def authenticate(self, user=None):
        user = user or self.user
        response = self.client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": "Password123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

    def test_b2c_agreement_flow_accepts_checkboxes_signs_and_verifies(self):
        self.authenticate()

        accept_response = self.client.post(
            "/api/v1/agreements/accept",
            {
                "privacy_terms_accepted": True,
                "ai_disclosure_accepted": True,
            },
            format="json",
        )
        self.assertEqual(accept_response.status_code, status.HTTP_201_CREATED, accept_response.data)
        self.assertEqual(Agreement.objects.filter(user=self.user, agreement_type=AgreementType.PRIVACY_TERMS).count(), 1)

        initiate_response = self.client.post(
            "/api/v1/agreements/sign/initiate",
            {
                "agreement_type": AgreementType.B2C_AGREEMENT,
                "signatory_name": self.user.get_full_name(),
            },
            format="json",
        )
        self.assertEqual(initiate_response.status_code, status.HTTP_201_CREATED, initiate_response.data)
        agreement = Agreement.objects.get(public_id=initiate_response.data["id"])

        confirm_response = self.client.post(
            "/api/v1/agreements/sign/confirm",
            {
                "agreement_id": str(agreement.public_id),
                "otp_code": agreement.otp_code,
            },
            format="json",
        )
        self.assertEqual(confirm_response.status_code, status.HTTP_200_OK, confirm_response.data)
        agreement.refresh_from_db()
        self.assertEqual(agreement.status, "signed")
        self.assertTrue(agreement.pdf_path)
        self.assertTrue(agreement.pdf_hash)
        self.assertGreaterEqual(len(mail.outbox), 2)
        self.assertTrue(any(message.attachments for message in mail.outbox))

        version_response = self.client.get("/api/v1/agreements/version-check")
        self.assertEqual(version_response.status_code, status.HTTP_200_OK, version_response.data)
        self.assertEqual(version_response.data["mismatches"], [])

        download_response = self.client.get(f"/api/v1/agreements/download/{agreement.public_id}")
        self.assertEqual(download_response.status_code, status.HTTP_200_OK)
        self.assertEqual(download_response["Content-Type"], "application/pdf")

        verify_response = self.client.get(f"/api/v1/agreements/verify/{agreement.public_id}")
        self.assertEqual(verify_response.status_code, status.HTTP_200_OK, verify_response.data)
        self.assertEqual(verify_response.data["agreement_id"], agreement.agreement_id)

    def test_b2b_signing_requires_authorization_checkbox(self):
        b2b_user = User.objects.create_user(
            email="b2b@example.com",
            password="Password123!",
            first_name="Company",
            last_name="Admin",
            role=Roles.B2B,
            is_verified=True,
        )
        self.authenticate(b2b_user)

        response = self.client.post(
            "/api/v1/agreements/sign/initiate",
            {
                "agreement_type": AgreementType.B2B_AGREEMENT,
                "signatory_name": b2b_user.get_full_name(),
                "auth_checkbox_confirmed": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Authorization checkbox", str(response.data))

    def test_candidate_consent_privacy_notice_verbal_and_device_endpoints(self):
        token_client = APIClient()
        blocked_start = token_client.post(
            f"/api/v1/interviews/{self.session.public_id}/start/",
            {"token": self.session.access_token},
            format="json",
            HTTP_X_SESSION_TOKEN=self.session.access_token,
        )
        self.assertEqual(blocked_start.status_code, status.HTTP_400_BAD_REQUEST, blocked_start.data)

        initiate_response = self.client.post(
            "/api/v1/agreements/sign/initiate",
            {
                "agreement_type": AgreementType.CANDIDATE_CONSENT,
                "signatory_name": self.candidate.get_full_name(),
                "session_id": str(self.session.public_id),
                "token": self.session.access_token,
            },
            format="json",
        )
        self.assertEqual(initiate_response.status_code, status.HTTP_201_CREATED, initiate_response.data)
        agreement = Agreement.objects.get(public_id=initiate_response.data["id"])

        confirm_response = self.client.post(
            "/api/v1/agreements/sign/confirm",
            {
                "agreement_id": str(agreement.public_id),
                "otp_code": agreement.otp_code,
            },
            format="json",
        )
        self.assertEqual(confirm_response.status_code, status.HTTP_200_OK, confirm_response.data)

        identity_response = self.client.post(
            "/api/v1/candidate/identity-verification",
            {
                "session_id": str(self.session.public_id),
                "token": self.session.access_token,
                "face_match_score": "91.50",
                "single_face_detected": True,
            },
            format="json",
        )
        self.assertEqual(identity_response.status_code, status.HTTP_200_OK, identity_response.data)

        privacy_response = self.client.post(
            "/api/v1/candidate/privacy-notice",
            {
                "session_id": str(self.session.public_id),
                "token": self.session.access_token,
            },
            format="json",
        )
        self.assertEqual(privacy_response.status_code, status.HTTP_200_OK, privacy_response.data)

        device_response = self.client.post(
            "/api/v1/candidate/device-check",
            {
                "session_id": str(self.session.public_id),
                "token": self.session.access_token,
            },
            format="json",
        )
        self.assertEqual(device_response.status_code, status.HTTP_200_OK, device_response.data)

        verbal_response = self.client.post(
            "/api/v1/candidate/verbal-confirmation",
            {
                "session_id": str(self.session.public_id),
                "token": self.session.access_token,
                "audio_file": make_file("verbal.webm", content=b"audio", content_type="audio/webm"),
            },
            format="multipart",
        )
        self.assertEqual(verbal_response.status_code, status.HTTP_201_CREATED, verbal_response.data)

        self.session.refresh_from_db()
        self.assertEqual(self.session.candidate_consent_agreement_id, agreement.id)
        self.assertTrue(self.session.privacy_notice_acknowledged_at)
        self.assertTrue(self.session.device_check_completed_at)
        self.assertTrue(self.session.verbal_confirmation_recorded_at)

        start_response = token_client.post(
            f"/api/v1/interviews/{self.session.public_id}/start/",
            {"token": self.session.access_token},
            format="json",
            HTTP_X_SESSION_TOKEN=self.session.access_token,
        )
        self.assertEqual(start_response.status_code, status.HTTP_200_OK, start_response.data)
        self.assertEqual(start_response.data["status"], "IN_PROGRESS")

    def test_cookie_consent_can_be_recorded_and_retrieved(self):
        self.authenticate()
        create_response = self.client.post(
            "/api/v1/cookies/consent",
            {
                "visitor_key": "anon-123",
                "categories_accepted": {
                    "strictly_necessary": True,
                    "functional": False,
                    "analytics": True,
                }
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        self.assertEqual(CookieConsent.objects.filter(user=self.user).count(), 1)

        get_response = self.client.get(f"/api/v1/cookies/consent/{self.user.public_id}")
        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.data)
        self.assertTrue(get_response.data["categories_accepted"]["analytics"])

        self.client.credentials()
        anonymous_get = self.client.get("/api/v1/cookies/consent?visitor_key=anon-123")
        self.assertEqual(anonymous_get.status_code, status.HTTP_200_OK, anonymous_get.data)
        self.assertEqual(anonymous_get.data["visitor_key"], "anon-123")

    def test_invalid_otp_increments_failed_attempts_and_locks_after_five_tries(self):
        self.authenticate()
        initiate_response = self.client.post(
            "/api/v1/agreements/sign/initiate",
            {
                "agreement_type": AgreementType.B2C_AGREEMENT,
                "signatory_name": self.user.get_full_name(),
            },
            format="json",
        )
        self.assertEqual(initiate_response.status_code, status.HTTP_201_CREATED, initiate_response.data)
        agreement = Agreement.objects.get(public_id=initiate_response.data["id"])
        wrong_code = "111111" if agreement.otp_code != "111111" else "222222"

        response = None
        for _ in range(5):
            response = self.client.post(
                "/api/v1/agreements/sign/confirm",
                {
                    "agreement_id": str(agreement.public_id),
                    "otp_code": wrong_code,
                },
                format="json",
            )

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        agreement.refresh_from_db()
        self.assertEqual(agreement.otp_failed_attempts, 5)
        self.assertEqual(agreement.status, "pending_review")
