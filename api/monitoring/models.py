from django.conf import settings
from django.db import models

from api.core.models import TimeStampedModel


class ServiceIncident(TimeStampedModel):
    """A platform problem that prevents a reliable assessment outcome
    (rulebook EXC-06). Opened automatically, worked by an administrator.

    One open incident per session and cause: repeat failures increase
    occurrence_count instead of opening (and emailing) again."""

    CAUSE_SCORING_FAILED = "SCORING_FAILED"
    CAUSE_REPORT_FAILED = "REPORT_FAILED"
    CAUSE_CERTIFICATE_FAILED = "CERTIFICATE_FAILED"
    CAUSE_AI_PROCESSING_FAILED = "AI_PROCESSING_FAILED"
    CAUSE_QUEUE_POISONED = "QUEUE_POISONED"
    CAUSE_CHOICES = (
        (CAUSE_SCORING_FAILED, "Scoring failed"),
        (CAUSE_REPORT_FAILED, "Report generation failed"),
        (CAUSE_CERTIFICATE_FAILED, "Certificate generation failed"),
        (CAUSE_AI_PROCESSING_FAILED, "AI processing failed for an answer"),
        (CAUSE_QUEUE_POISONED, "Background job exhausted its retries"),
    )

    STATUS_OPEN = "OPEN"
    STATUS_IN_PROGRESS = "IN_PROGRESS"
    STATUS_RESOLVED = "RESOLVED"
    STATUS_CHOICES = (
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_RESOLVED, "Resolved"),
    )
    ACTIVE_STATUSES = (STATUS_OPEN, STATUS_IN_PROGRESS)

    cause = models.CharField(max_length=40, choices=CAUSE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    affected_output = models.CharField(max_length=60, blank=True)
    session = models.ForeignKey(
        "interview_sessions.InterviewSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="service_incidents",
    )
    evaluation = models.ForeignKey(
        "evaluations.Evaluation", on_delete=models.SET_NULL, null=True, blank=True, related_name="service_incidents",
    )
    company = models.ForeignKey(
        "accounts.Company", on_delete=models.SET_NULL, null=True, blank=True, related_name="service_incidents",
    )
    account_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="service_incidents_affecting",
    )
    summary = models.CharField(max_length=255)
    details = models.TextField(blank=True)
    next_action = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="service_incidents_owned",
    )
    deadline = models.DateTimeField(null=True, blank=True)
    occurrence_count = models.PositiveIntegerField(default=1)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="service_incidents_resolved",
    )
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["cause", "status"])]

    def __str__(self):
        return f"{self.get_cause_display()} ({self.status})"
