from django.db import models
from django.utils import timezone

from api.core.models import TimeStampedModel


class CandidateResponseTranslation(TimeStampedModel):
    response = models.OneToOneField(
        "interview_sessions.CandidateResponse",
        on_delete=models.CASCADE,
        related_name="translation_artifact",
    )
    session = models.ForeignKey(
        "interview_sessions.InterviewSession",
        on_delete=models.CASCADE,
        related_name="response_translations",
    )
    question = models.ForeignKey(
        "interview_sessions.SessionQuestion",
        on_delete=models.CASCADE,
        related_name="response_translations",
    )
    source_language = models.CharField(max_length=20, blank=True)
    target_language = models.CharField(max_length=20, blank=True)
    original_transcript = models.TextField(blank=True)
    translated_transcript = models.TextField(blank=True)
    provider = models.CharField(max_length=100, blank=True)
    provider_model = models.CharField(max_length=120, blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=30, default="NOT_STARTED")
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    translated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Candidate Response Translation"
        verbose_name_plural = "Candidate Response Translations"
        indexes = [
            models.Index(fields=["session", "status"]),
            models.Index(fields=["provider", "translated_at"]),
        ]

    def __str__(self):
        return f"{self.response_id} - {self.source_language}->{self.target_language}"


class CandidateResponseInterpretation(TimeStampedModel):
    response = models.OneToOneField(
        "interview_sessions.CandidateResponse",
        on_delete=models.CASCADE,
        related_name="ai_interpretation",
    )
    session = models.ForeignKey(
        "interview_sessions.InterviewSession",
        on_delete=models.CASCADE,
        related_name="response_interpretations",
    )
    question = models.ForeignKey(
        "interview_sessions.SessionQuestion",
        on_delete=models.CASCADE,
        related_name="response_interpretations",
    )
    provider = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=120, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True)
    prompt_hash = models.CharField(max_length=64, blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True)
    input_transcript_type = models.CharField(max_length=30, blank=True)
    input_language = models.CharField(max_length=20, blank=True)
    input_transcript = models.TextField(blank=True)
    structured_output = models.JSONField(default=dict, blank=True)
    normalized_indicators = models.JSONField(default=dict, blank=True)
    risk_flags = models.JSONField(default=list, blank=True)
    confidence_score = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    status = models.CharField(max_length=30, default="NOT_STARTED")
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    interpreted_at = models.DateTimeField(null=True, blank=True)
    legal_disclaimer = models.TextField(blank=True)

    class Meta:
        verbose_name = "Candidate Response Interpretation"
        verbose_name_plural = "Candidate Response Interpretations"
        indexes = [
            models.Index(fields=["session", "status"]),
            models.Index(fields=["provider", "interpreted_at"]),
        ]

    def __str__(self):
        return f"{self.response_id} - {self.status}"


class EvaluationInputArtifact(TimeStampedModel):
    response = models.OneToOneField(
        "interview_sessions.CandidateResponse",
        on_delete=models.CASCADE,
        related_name="evaluation_input_artifact",
    )
    session = models.ForeignKey(
        "interview_sessions.InterviewSession",
        on_delete=models.CASCADE,
        related_name="evaluation_input_artifacts",
    )
    question = models.ForeignKey(
        "interview_sessions.SessionQuestion",
        on_delete=models.CASCADE,
        related_name="evaluation_input_artifacts",
    )
    competency_code = models.CharField(max_length=150, blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True)
    expected_indicators = models.JSONField(default=list, blank=True)
    observed_indicators = models.JSONField(default=list, blank=True)
    missing_indicators = models.JSONField(default=list, blank=True)
    risk_flags = models.JSONField(default=list, blank=True)
    language_notes = models.JSONField(default=dict, blank=True)
    source_interpretation_status = models.CharField(max_length=30, blank=True)
    requires_human_review = models.BooleanField(default=False)
    review_reason = models.TextField(blank=True)
    legal_disclaimer = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    prepared_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Evaluation Input Artifact"
        verbose_name_plural = "Evaluation Input Artifacts"
        indexes = [
            models.Index(fields=["session", "source_interpretation_status"]),
            models.Index(fields=["competency_code", "prepared_at"]),
        ]

    def __str__(self):
        return f"{self.response_id} - {self.competency_code}"


class AIProcessingJob(TimeStampedModel):
    response = models.ForeignKey(
        "interview_sessions.CandidateResponse",
        on_delete=models.CASCADE,
        related_name="ai_processing_jobs",
    )
    session = models.ForeignKey(
        "interview_sessions.InterviewSession",
        on_delete=models.CASCADE,
        related_name="ai_processing_jobs",
    )
    question = models.ForeignKey(
        "interview_sessions.SessionQuestion",
        on_delete=models.CASCADE,
        related_name="ai_processing_jobs",
    )
    job_type = models.CharField(max_length=50, default="PROCESS_AI")
    status = models.CharField(max_length=30, default="PENDING")
    idempotency_key = models.CharField(max_length=120)
    queue_name = models.CharField(max_length=120, blank=True)
    queue_message_id = models.CharField(max_length=120, blank=True)
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "AI Processing Job"
        verbose_name_plural = "AI Processing Jobs"
        unique_together = [("response", "idempotency_key")]
        indexes = [
            models.Index(fields=["session", "status"]),
            models.Index(fields=["idempotency_key", "status"]),
        ]

    def __str__(self):
        return f"{self.response_id} - {self.status} - {self.idempotency_key}"
