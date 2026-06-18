from django.db import models

from api.core.constants import InterviewEvaluationTier
from api.core.models import TimeStampedModel


class InterviewConfiguration(TimeStampedModel):
    role_name = models.CharField(max_length=100)
    role_code = models.CharField(max_length=100, blank=True)
    language = models.CharField(max_length=20, default="EN")
    evaluation_tier = models.CharField(
        max_length=20,
        choices=InterviewEvaluationTier.CHOICES,
        default=InterviewEvaluationTier.FULL,
    )
    duration_minutes = models.PositiveIntegerField(default=30)
    total_questions = models.PositiveIntegerField(default=10)
    allow_retries = models.BooleanField(default=True)
    max_retries = models.PositiveIntegerField(default=1)
    enable_translation = models.BooleanField(default=False)
    enable_task_module = models.BooleanField(default=False)
    enable_integrity_checks = models.BooleanField(default=False)
    rubric_version = models.CharField(max_length=20, blank=True)
    question_set_version = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Interview Configuration"
        verbose_name_plural = "Interview Configurations"
        ordering = ["role_name", "language", "evaluation_tier"]

    def __str__(self):
        return f"{self.role_name} ({self.language} / {self.evaluation_tier})"


class InterviewRubric(TimeStampedModel):
    role_name = models.CharField(max_length=100)
    role_code = models.CharField(max_length=100, blank=True)
    skill_tag = models.CharField(max_length=150)
    scoring_category = models.CharField(max_length=100)
    weight = models.DecimalField(max_digits=6, decimal_places=4)
    max_score = models.PositiveIntegerField()
    scoring_type = models.CharField(max_length=50)
    domain = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    rubric_version = models.CharField(max_length=20, blank=True)
    question_set_version = models.CharField(max_length=20, blank=True)
    evaluation_criteria = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Interview Rubric"
        verbose_name_plural = "Interview Rubrics"
        unique_together = [("role_code", "skill_tag", "rubric_version")]
        ordering = ["role_name", "skill_tag"]

    def __str__(self):
        return f"{self.role_name} - {self.skill_tag}"
