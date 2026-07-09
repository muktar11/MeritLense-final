import secrets
from datetime import timedelta
from pathlib import Path

from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from api.accounts.models import User
from api.candidates.models import Candidate
from api.core.constants import (
    CandidateResponseType,
    CoverageLevel,
    IdentityVerificationStatus,
    InterviewSessionStatus,
    SessionQuestionStatus,
)
from api.core.models import SoftDeleteModel, TimeStampedModel


def response_audio_upload_to(instance, filename):
    extension = Path(filename).suffix or ".bin"
    return f"interviews/sessions/{instance.session_id}/responses/{instance.public_id}{extension}"


def question_audio_upload_to(instance, filename):
    extension = Path(filename).suffix or ".bin"
    return f"interviews/sessions/{instance.session_id}/questions/{instance.question_id}/tts/{instance.public_id}{extension}"


class InterviewSession(TimeStampedModel, SoftDeleteModel):
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="interview_sessions",
    )
    organization = models.ForeignKey(
        "accounts.Company",
        on_delete=models.CASCADE,
        related_name="interview_sessions",
        null=True,
        blank=True,
    )
    config = models.ForeignKey(
        "interviews.InterviewConfiguration",
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    package_session_config = models.ForeignKey(
        "interviews.PackageSessionConfig",
        on_delete=models.SET_NULL,
        related_name="sessions",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=30,
        choices=InterviewSessionStatus.CHOICES,
        default=InterviewSessionStatus.CREATED,
    )
    role_name = models.CharField(max_length=100)
    role_code = models.CharField(max_length=100, blank=True)
    ui_language = models.CharField(max_length=20, default="EN")
    candidate_language = models.CharField(max_length=20, default="EN")
    tts_language_code = models.CharField(max_length=20, default="en-US")
    stt_language_code = models.CharField(max_length=20, default="en-US")
    translation_target = models.CharField(max_length=20, blank=True)
    current_question_index = models.PositiveIntegerField(default=0)
    total_questions = models.PositiveIntegerField(default=0)
    evaluation_tier = models.CharField(max_length=20, default="FULL")
    package_code = models.CharField(max_length=50, blank=True)
    package_name = models.CharField(max_length=100, blank=True)
    coverage_level = models.CharField(
        max_length=20,
        choices=CoverageLevel.CHOICES,
        default=CoverageLevel.FULL,
    )
    task_observation_enabled = models.BooleanField(default=False)
    readiness_indicator_enabled = models.BooleanField(default=True)
    certificate_enabled = models.BooleanField(default=True)
    rubric_version = models.CharField(max_length=20, blank=True)
    question_set_version = models.CharField(max_length=20, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="created_interview_sessions",
    )
    access_token = models.CharField(max_length=64, unique=True, editable=False)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    identity_verified = models.BooleanField(default=False)
    face_match_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    single_face_detected = models.BooleanField(default=False)
    verification_status = models.CharField(
        max_length=20,
        choices=IdentityVerificationStatus.CHOICES,
        default=IdentityVerificationStatus.NOT_STARTED,
    )
    candidate_consent_agreement = models.ForeignKey(
        "contracts.Agreement",
        on_delete=models.SET_NULL,
        related_name="linked_sessions",
        null=True,
        blank=True,
    )
    verbal_confirmation_path = models.TextField(blank=True)
    verbal_confirmation_recorded_at = models.DateTimeField(null=True, blank=True)
    privacy_notice_acknowledged_at = models.DateTimeField(null=True, blank=True)
    privacy_notice_ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_check_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Interview Session"
        verbose_name_plural = "Interview Sessions"
        indexes = [
            models.Index(fields=["candidate", "status"]),
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["created_by", "-created_at"]),
            models.Index(fields=["expires_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.candidate.get_full_name()} - {self.status}"

    def save(self, *args, **kwargs):
        creating = self._state.adding
        if creating and not self.role_name:
            self.role_name = self.config.role_name
        if creating and not self.organization and self.candidate.company:
            self.organization = self.candidate.company
        if creating and not self.access_token:
            self.access_token = secrets.token_urlsafe(32)
        if creating and not self.token_expires_at:
            self.token_expires_at = self.expires_at
        if creating and not self.total_questions:
            self.total_questions = self.config.total_questions
        super().save(*args, **kwargs)

    def is_expired(self):
        return timezone.now() >= self.expires_at

    def is_closed(self):
        return self.status in {
            InterviewSessionStatus.COMPLETED,
            InterviewSessionStatus.FAILED,
            InterviewSessionStatus.EXPIRED,
            InterviewSessionStatus.CANCELLED,
        }

    def can_manage(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.role in {"ADMIN", "SUPERADMIN"}:
            return True
        if self.created_by_id == user.id:
            return True
        if hasattr(user, "managed_company") and self.organization_id == user.managed_company.id:
            return True
        if user.role == "B2B_TEAM_MEMBER" and user in self.candidate.shared_with.all():
            return True
        return False

    def token_is_valid(self, token):
        if not token:
            return False
        if token != self.access_token:
            return False
        if self.token_expires_at and timezone.now() > self.token_expires_at:
            return False
        return True

    def mark_verification_pending(self):
        self.status = InterviewSessionStatus.VERIFICATION_PENDING
        self.save(update_fields=["status", "updated_at"])

    def mark_ready(self):
        self.status = InterviewSessionStatus.READY
        self.save(update_fields=["status", "updated_at"])

    def candidate_prechecks_complete(self):
        return bool(
            self.candidate_consent_agreement_id
            and self.identity_verified
            and self.face_match_score is not None
            and float(self.face_match_score) >= 85.0
            and self.device_check_completed_at
            and self.verbal_confirmation_recorded_at
            and self.privacy_notice_acknowledged_at
        )

    def start(self):
        if self.is_expired():
            self.status = InterviewSessionStatus.EXPIRED
            self.save(update_fields=["status", "updated_at"])
            raise ValueError("Cannot start expired session")
        if self.status == InterviewSessionStatus.COMPLETED:
            raise ValueError("Cannot restart completed session")
        if self.started_at is None:
            self.started_at = timezone.now()
        self.last_activity_at = timezone.now()
        self.status = InterviewSessionStatus.IN_PROGRESS
        self.save(update_fields=["status", "started_at", "last_activity_at", "updated_at"])

    def complete(self):
        if self.is_closed():
            raise ValueError("Session is already closed")
        self.status = InterviewSessionStatus.COMPLETED
        self.ended_at = timezone.now()
        self.last_activity_at = self.ended_at
        self.save(update_fields=["status", "ended_at", "last_activity_at", "updated_at"])

    @classmethod
    def build_expiry(cls, duration_minutes, buffer_hours=24):
        return timezone.now() + timedelta(minutes=duration_minutes, hours=buffer_hours)


class SessionQuestion(TimeStampedModel):
    session = models.ForeignKey(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    question_template = models.ForeignKey(
        "questions.QuestionTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="session_questions",
    )
    question_text = models.TextField()
    domain = models.CharField(max_length=100, blank=True)
    skill = models.CharField(max_length=100, blank=True)
    difficulty = models.CharField(max_length=20, blank=True)
    question_order = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=SessionQuestionStatus.CHOICES,
        default=SessionQuestionStatus.PENDING,
    )
    is_mandatory = models.BooleanField(default=True)
    asked_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Session Question"
        verbose_name_plural = "Session Questions"
        unique_together = [("session", "question_order")]
        indexes = [
            models.Index(fields=["session", "question_order"]),
            models.Index(fields=["session", "status"]),
        ]
        ordering = ["question_order"]

    def __str__(self):
        return f"{self.session_id} - Q{self.question_order}"


class CandidateResponse(TimeStampedModel):
    session = models.ForeignKey(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    question = models.ForeignKey(
        SessionQuestion,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    response_type = models.CharField(
        max_length=20,
        choices=CandidateResponseType.CHOICES,
        default=CandidateResponseType.TEXT,
    )
    audio_file = models.FileField(upload_to=response_audio_upload_to, blank=True)
    audio_url = models.URLField(blank=True)
    audio_mime_type = models.CharField(max_length=100, blank=True)
    audio_file_size_bytes = models.PositiveIntegerField(default=0)
    audio_uploaded_at = models.DateTimeField(null=True, blank=True)
    text_response = models.TextField(blank=True)
    transcript = models.TextField(blank=True)
    original_transcript = models.TextField(blank=True)
    transcript_language = models.CharField(max_length=20, blank=True)
    stt_provider = models.CharField(max_length=100, blank=True)
    stt_model = models.CharField(max_length=100, blank=True)
    stt_request_id = models.CharField(max_length=150, blank=True)
    stt_confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    stt_status = models.CharField(max_length=20, default="PENDING")
    stt_error_code = models.CharField(max_length=100, blank=True)
    stt_error_message = models.TextField(blank=True)
    stt_processed_at = models.DateTimeField(null=True, blank=True)
    stt_metadata = models.JSONField(default=dict, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    attempt_number = models.PositiveIntegerField(default=1)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Candidate Response"
        verbose_name_plural = "Candidate Responses"
        indexes = [
            models.Index(fields=["session", "question"]),
            models.Index(fields=["response_type"]),
        ]
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.session_id} - Q{self.question_id} - {self.response_type}"


class QuestionAudioArtifact(TimeStampedModel):
    session = models.ForeignKey(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="question_audio_artifacts",
    )
    question = models.ForeignKey(
        SessionQuestion,
        on_delete=models.CASCADE,
        related_name="audio_artifacts",
    )
    provider = models.CharField(max_length=100)
    voice_name = models.CharField(max_length=120)
    language_code = models.CharField(max_length=20)
    audio_file = models.FileField(upload_to=question_audio_upload_to)
    audio_url = models.URLField(blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    file_size_bytes = models.PositiveIntegerField(default=0)
    duration_estimate_seconds = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Question Audio Artifact"
        verbose_name_plural = "Question Audio Artifacts"
        unique_together = [("session", "question", "language_code", "voice_name")]
        indexes = [
            models.Index(fields=["session", "question"]),
            models.Index(fields=["provider", "generated_at"]),
        ]
        ordering = ["question__question_order", "-generated_at"]

    def __str__(self):
        return f"{self.session_id} - Q{self.question_id} - {self.language_code}"


class IntegrityLog(TimeStampedModel):
    session = models.ForeignKey(
        InterviewSession,
        on_delete=models.CASCADE,
        related_name="integrity_logs",
    )
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="integrity_logs",
    )
    event_type = models.CharField(max_length=100)
    severity = models.CharField(max_length=20, default="INFO")
    details = models.JSONField(default=dict, blank=True)
    detected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Integrity Log"
        verbose_name_plural = "Integrity Logs"
        indexes = [
            models.Index(fields=["session", "-detected_at"]),
            models.Index(fields=["candidate", "-detected_at"]),
            models.Index(fields=["event_type"]),
        ]
        ordering = ["-detected_at"]

    def __str__(self):
        return f"{self.session_id} - {self.event_type}"
