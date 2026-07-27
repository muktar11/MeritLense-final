from django.core.exceptions import ValidationError
from django.db import DatabaseError, models
from django.utils import timezone

from api.core.models import TimeStampedModel


def report_pdf_upload_to(instance, filename):
    return f"reports/evaluations/{instance.evaluation_id}/{instance.report_number}.pdf"


class EvaluationReportQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise DatabaseError("Generated evaluation reports are immutable.")

    def delete(self):
        raise DatabaseError("Generated evaluation reports cannot be deleted.")


class EvaluationReport(TimeStampedModel):
    STATUS_PENDING = "PENDING"
    STATUS_ACTIVE = "ACTIVE"
    STATUS_SUPERSEDED = "SUPERSEDED"
    STATUS_REVOKED = "REVOKED"
    STATUS_FAILED = "FAILED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUPERSEDED, "Superseded"),
        (STATUS_REVOKED, "Revoked"),
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
    report_version = models.CharField(max_length=40, default="1.3")
    report_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    overall_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    max_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    overall_percentage = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    readiness_status = models.CharField(max_length=20, blank=True)
    readiness_indicator = models.CharField(max_length=20, blank=True)
    readiness_reason = models.TextField(blank=True)
    override_triggered = models.BooleanField(default=False)
    rule_engine_version = models.CharField(max_length=40, blank=True)
    requires_human_review = models.BooleanField(default=False)
    scoring_rule_set_name = models.CharField(max_length=150, blank=True)
    scoring_rule_version = models.CharField(max_length=40, blank=True)
    employer_pdf = models.FileField(upload_to=report_pdf_upload_to, null=True, blank=True)
    pdf_hash = models.CharField(max_length=64, blank=True)
    report_payload = models.JSONField(default=dict, blank=True)
    competency_breakdown = models.JSONField(default=list, blank=True)
    response_evidence_summary = models.JSONField(default=list, blank=True)
    human_review_flags = models.JSONField(default=list, blank=True)
    critical_failures = models.JSONField(default=list, blank=True)
    readiness_legal_record = models.ForeignKey(
        "evaluations.EvaluationReadinessDecisionRecord",
        on_delete=models.PROTECT,
        related_name="linked_reports",
        null=True,
        blank=True,
    )
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

    objects = EvaluationReportQuerySet.as_manager()

    def __str__(self):
        return f"{self.report_number} - {self.report_status}"

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            existing = type(self).objects.get(pk=self.pk)
            allowed_stale_transition = (
                existing.report_status == self.STATUS_ACTIVE
                and self.report_status == self.STATUS_SUPERSEDED
                and existing.report_number == self.report_number
                and existing.report_version == self.report_version
                and existing.evaluation_id == self.evaluation_id
                and existing.session_id == self.session_id
                and existing.candidate_id == self.candidate_id
                and existing.overall_score == self.overall_score
                and existing.max_score == self.max_score
                and existing.overall_percentage == self.overall_percentage
                and existing.readiness_status == self.readiness_status
                and existing.readiness_indicator == self.readiness_indicator
                and existing.readiness_reason == self.readiness_reason
                and existing.override_triggered == self.override_triggered
                and existing.rule_engine_version == self.rule_engine_version
                and existing.requires_human_review == self.requires_human_review
                and existing.scoring_rule_set_name == self.scoring_rule_set_name
                and existing.scoring_rule_version == self.scoring_rule_version
                and existing.employer_pdf == self.employer_pdf
                and existing.pdf_hash == self.pdf_hash
                and existing.report_payload == self.report_payload
                and existing.competency_breakdown == self.competency_breakdown
                and existing.response_evidence_summary == self.response_evidence_summary
                and existing.human_review_flags == self.human_review_flags
                and existing.critical_failures == self.critical_failures
                and existing.readiness_legal_record_id == self.readiness_legal_record_id
                and existing.generated_by_id == self.generated_by_id
                and existing.generated_at == self.generated_at
                and existing.metadata == self.metadata
            )
            if not allowed_stale_transition:
                raise ValidationError(
                    "Generated evaluation reports are immutable. Regeneration must create a new report version."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Generated evaluation reports cannot be deleted.")
