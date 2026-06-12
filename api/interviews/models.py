from django.db import models

from api.core.models import TimeStampedModel


class InterviewConfiguration(TimeStampedModel):
    role_name = models.CharField(max_length=100)
    language = models.CharField(max_length=20, default="EN")
    duration_minutes = models.PositiveIntegerField(default=30)
    total_questions = models.PositiveIntegerField(default=10)
    allow_retries = models.BooleanField(default=True)
    max_retries = models.PositiveIntegerField(default=1)
    enable_translation = models.BooleanField(default=False)
    enable_task_module = models.BooleanField(default=False)
    enable_integrity_checks = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Interview Configuration"
        verbose_name_plural = "Interview Configurations"
        ordering = ["role_name", "language"]

    def __str__(self):
        return f"{self.role_name} ({self.language})"
