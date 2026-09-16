import shutil
import tempfile
from datetime import timedelta
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from api.accounts.models import User
from api.audit.models import AuditLog
from api.candidates.models import Candidate
from api.core.constants import EvaluationStatus, EvaluationType, InterviewEvaluationTier, InterviewSessionStatus, Roles
from api.evaluations.models import Evaluation
from api.interviews.models import InterviewConfiguration
from api.payments.models import PackageBalance, SlotReservation
from api.sessions.models import CandidateResponse, InterviewSession, SessionArtifact, SessionQuestion
from api.sessions.services import InterviewSessionService


def make_file(name="doc.pdf", content=b"%PDF-1.1 test content"):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


class PurgeExpiredMediaCommandTests(TestCase):
    """Covers the DPA-driven 90-day raw-media retention job - only audio and
    identity-verification image/document files should ever be removed, and
    only once their evaluation is well past the retention window. Transcripts,
    evidence, and verification status must survive untouched."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_dir = tempfile.mkdtemp(prefix="retention-tests-")
        cls._override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            email="retention-owner@example.com",
            password="testpass123",
            first_name="Owner",
            last_name="User",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Ret",
            last_name="Candidate",
            email="retention-candidate@example.com",
            passport_id="PASS-RET-001",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
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

    def _make_session_and_evaluation(self, *, completed_at, with_audio=True, with_artifact=True):
        session = InterviewSession.objects.create(
            candidate=self.candidate,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            expires_at=timezone.now() + timedelta(days=1),
            created_by=self.user,
        )
        question = SessionQuestion.objects.create(
            session=session,
            question_text="What should you check first?",
            question_order=1,
        )
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            transcript="I would check the smoke detectors first.",
        )
        if with_audio:
            response.audio_file.save("response.mp3", make_file("response.mp3", b"fake-audio"), save=True)

        artifact = None
        if with_artifact:
            artifact = SessionArtifact.objects.create(
                session=session,
                candidate=self.candidate,
                artifact_type="ID_DOCUMENT",
                file=make_file("id-doc.pdf"),
            )

        evaluation = Evaluation.objects.create(
            session=session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            status=EvaluationStatus.COMPLETED,
            scheduled_date=timezone.now() - timedelta(days=1),
            completed_at=completed_at,
            created_by=self.user,
        )
        return session, response, artifact, evaluation

    def test_deletes_audio_and_artifacts_past_retention_window(self):
        _, response, artifact, evaluation = self._make_session_and_evaluation(
            completed_at=timezone.now() - timedelta(days=100),
        )
        audio_path = response.audio_file.path
        artifact_path = artifact.file.path
        self.assertTrue(response.audio_file.storage.exists(audio_path))
        self.assertTrue(artifact.file.storage.exists(artifact_path))

        call_command("purge_expired_media")

        response.refresh_from_db()
        artifact.refresh_from_db()
        self.assertFalse(response.audio_file)
        self.assertFalse(artifact.file)
        self.assertFalse(response.audio_file.storage.exists(audio_path))
        self.assertFalse(artifact.file.storage.exists(artifact_path))

        # The evidence that matters for the evaluation record survives untouched.
        self.assertEqual(response.transcript, "I would check the smoke detectors first.")

        log = AuditLog.objects.filter(user_role="SYSTEM").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.data.get("evaluation_id"), evaluation.id)
        self.assertEqual(log.data.get("audio_files_deleted"), 1)
        self.assertEqual(log.data.get("artifacts_deleted"), 1)

    def test_leaves_recent_evaluations_untouched(self):
        _, response, artifact, _ = self._make_session_and_evaluation(
            completed_at=timezone.now() - timedelta(days=10),
        )
        call_command("purge_expired_media")

        response.refresh_from_db()
        artifact.refresh_from_db()
        self.assertTrue(response.audio_file)
        self.assertTrue(artifact.file)

    def test_leaves_incomplete_evaluations_untouched(self):
        _, response, artifact, evaluation = self._make_session_and_evaluation(completed_at=None)
        evaluation.status = EvaluationStatus.IN_PROGRESS
        evaluation.save(update_fields=["status"])

        call_command("purge_expired_media")

        response.refresh_from_db()
        artifact.refresh_from_db()
        self.assertTrue(response.audio_file)
        self.assertTrue(artifact.file)

    def test_dry_run_reports_without_deleting(self):
        _, response, artifact, _ = self._make_session_and_evaluation(
            completed_at=timezone.now() - timedelta(days=100),
        )
        out = StringIO()
        call_command("purge_expired_media", "--dry-run", stdout=out)

        response.refresh_from_db()
        artifact.refresh_from_db()
        self.assertTrue(response.audio_file)
        self.assertTrue(artifact.file)
        self.assertIn("dry-run", out.getvalue())
        self.assertEqual(AuditLog.objects.filter(user_role="SYSTEM").count(), 0)

    def test_custom_retention_days_applies(self):
        _, response, artifact, _ = self._make_session_and_evaluation(
            completed_at=timezone.now() - timedelta(days=40),
        )
        # Wouldn't be touched under the 90-day default, but should be under 30.
        call_command("purge_expired_media", "--days", "30")

        response.refresh_from_db()
        artifact.refresh_from_db()
        self.assertFalse(response.audio_file)
        self.assertFalse(artifact.file)


class ExpireStaleSessionsCommandTests(TestCase):
    """Proactive expiry (Slot Reservation Lifecycle spec, Section 6.1) -
    a session nobody ever revisits after its invite window passes would
    otherwise leave its Slot Reserved forever, since the lazy
    check-on-access path in start_session only fires when someone tries
    to start it."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="expiry-owner@example.com",
            password="testpass123",
            first_name="Owner",
            last_name="User",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Exp",
            last_name="Candidate",
            email="expiry-candidate@example.com",
            passport_id="PASS-EXP-001",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
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
        self.balance = PackageBalance.objects.create(
            owner_user=self.user, balance_type=PackageBalance.SLOTS, fixed_amount=5, current_balance=5,
        )

    def test_expires_a_stale_session_and_releases_its_reservation(self):
        session = InterviewSessionService.create_session(candidate=self.candidate, config=self.config, created_by=self.user)
        session.expires_at = timezone.now() - timedelta(minutes=5)
        session.save(update_fields=["expires_at"])
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 4)

        call_command("expire_stale_sessions")

        session.refresh_from_db()
        reservation = SlotReservation.objects.get(session=session)
        self.balance.refresh_from_db()
        self.assertEqual(session.status, InterviewSessionStatus.EXPIRED)
        self.assertEqual(reservation.status, SlotReservation.RELEASED)
        self.assertEqual(self.balance.current_balance, 5)

    def test_does_not_touch_sessions_still_within_their_window(self):
        session = InterviewSessionService.create_session(candidate=self.candidate, config=self.config, created_by=self.user)

        call_command("expire_stale_sessions")

        session.refresh_from_db()
        self.assertNotEqual(session.status, InterviewSessionStatus.EXPIRED)
        self.assertEqual(SlotReservation.objects.get(session=session).status, SlotReservation.RESERVED)

    def test_never_expires_or_releases_an_already_started_session(self):
        session = InterviewSessionService.create_session(candidate=self.candidate, config=self.config, created_by=self.user)
        InterviewSessionService.start_session(session, actor=self.user)
        session.expires_at = timezone.now() - timedelta(minutes=5)
        session.save(update_fields=["expires_at"])
        self.balance.refresh_from_db()
        balance_after_start = self.balance.current_balance

        call_command("expire_stale_sessions")

        session.refresh_from_db()
        reservation = SlotReservation.objects.get(session=session)
        self.balance.refresh_from_db()
        self.assertEqual(session.status, InterviewSessionStatus.IN_PROGRESS)
        self.assertEqual(reservation.status, SlotReservation.CONSUMED)
        self.assertEqual(self.balance.current_balance, balance_after_start)

    def test_dry_run_changes_nothing(self):
        session = InterviewSessionService.create_session(candidate=self.candidate, config=self.config, created_by=self.user)
        session.expires_at = timezone.now() - timedelta(minutes=5)
        session.save(update_fields=["expires_at"])

        out = StringIO()
        call_command("expire_stale_sessions", "--dry-run", stdout=out)

        session.refresh_from_db()
        self.assertNotEqual(session.status, InterviewSessionStatus.EXPIRED)
        self.assertEqual(SlotReservation.objects.get(session=session).status, SlotReservation.RESERVED)
        self.assertIn("dry-run", out.getvalue())

    def test_expiring_the_same_session_twice_does_not_double_release(self):
        session = InterviewSessionService.create_session(candidate=self.candidate, config=self.config, created_by=self.user)
        session.expires_at = timezone.now() - timedelta(minutes=5)
        session.save(update_fields=["expires_at"])

        call_command("expire_stale_sessions")
        call_command("expire_stale_sessions")

        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 5)
