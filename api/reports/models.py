from django.db import models
from django.utils import timezone

from api.core.models import TimeStampedModel


class EvaluationReport(TimeStampedModel):
    STATUS_PENDING = "PENDING"
    STATUS_GENERATED = "GENERATED"
    STATUS_STALE = "STALE"
    STATUS_ARCHIVED = "ARCHIVED"
    STATUS_FAILED = "FAILED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_GENERATED, "Generated"),
        (STATUS_STALE, "Stale"),
        (STATUS_ARCHIVED, "Archived"),
        (STATUS_FAILED, "Failed"),
    ]

    evaluation = models.ForeignKey(
        "evaluations.Evaluation",
        on_delete=models.CASCADE,
        related_name="reports",
    )
    session = models.ForeignKey(
        "interview_sessions.InterviewSession",
        on_delete=models.PROTECT,
        related_name="reports",
    )
    candidate = models.ForeignKey(
        "candidates.Candidate",
        on_delete=models.PROTECT,
        related_name="evaluation_reports",
    )
    report_number = models.CharField(max_length=80, unique=True, db_index=True)
    report_version = models.CharField(max_length=40, default="week7-v1")
    report_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    overall_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    max_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    overall_percentage = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    readiness_status = models.CharField(max_length=20, blank=True)
    requires_human_review = models.BooleanField(default=False)
    scoring_rule_set_name = models.CharField(max_length=150, blank=True)
    scoring_rule_version = models.CharField(max_length=40, blank=True)
    report_payload = models.JSONField(default=dict, blank=True)
    competency_breakdown = models.JSONField(default=list, blank=True)
    response_evidence_summary = models.JSONField(default=list, blank=True)
    human_review_flags = models.JSONField(default=list, blank=True)
    critical_failures = models.JSONField(default=list, blank=True)
    generated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_evaluation_reports",
    )
    generated_at = models.DateTimeField(default=timezone.now)
    last_regenerated_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Evaluation Report"
        verbose_name_plural = "Evaluation Reports"
        indexes = [
            models.Index(fields=["evaluation", "generated_at"]),
            models.Index(fields=["session", "generated_at"]),
            models.Index(fields=["candidate", "generated_at"]),
            models.Index(fields=["report_status", "generated_at"]),
        ]
        ordering = ["-generated_at", "-created_at"]

    def __str__(self):
        return f"{self.report_number} - {self.report_status}"
