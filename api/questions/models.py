from django.db import models

from api.core.constants import QuestionDifficulty
from api.core.models import TimeStampedModel


class QuestionTemplate(TimeStampedModel):
    role_name = models.CharField(max_length=100)
    domain = models.CharField(max_length=100)
    skill = models.CharField(max_length=100)
    difficulty = models.CharField(
        max_length=20,
        choices=QuestionDifficulty.CHOICES,
        default=QuestionDifficulty.MEDIUM,
    )
    question_text = models.TextField()
    expected_steps = models.JSONField(default=list, blank=True)
    keywords = models.JSONField(default=list, blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    language = models.CharField(max_length=20, default="EN")
    is_mandatory = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Question Template"
        verbose_name_plural = "Question Templates"
        indexes = [
            models.Index(fields=["role_name", "language", "is_active"]),
            models.Index(fields=["domain", "skill"]),
            models.Index(fields=["difficulty"]),
        ]
        ordering = ["role_name", "domain", "skill", "created_at"]

    def __str__(self):
        return f"{self.role_name} - {self.skill} - {self.difficulty}"
