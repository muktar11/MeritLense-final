from django.db import models

from api.core.constants import (
    ExpectedAnswerType,
    InterviewEvaluationTier,
    InterviewQuestionFormat,
    InterviewQuestionType,
    QuestionDifficulty,
    QuestionLifecycleStatus,
)
from api.core.models import TimeStampedModel


class QuestionTemplate(TimeStampedModel):
    role_name = models.CharField(max_length=100)
    role_code = models.CharField(max_length=100, blank=True)
    question_code = models.CharField(max_length=50, blank=True)
    question_version = models.CharField(max_length=20, blank=True)
    question_status = models.CharField(
        max_length=20,
        choices=QuestionLifecycleStatus.CHOICES,
        default=QuestionLifecycleStatus.ACTIVE,
    )
    domain = models.CharField(max_length=100)
    skill_tag = models.CharField(max_length=150, blank=True, default="")
    skill_id = models.CharField(max_length=100, blank=True)
    skill = models.CharField(max_length=100)
    sequence_number = models.PositiveIntegerField(null=True, blank=True)
    difficulty = models.CharField(
        max_length=20,
        choices=QuestionDifficulty.CHOICES,
        default=QuestionDifficulty.MEDIUM,
    )
    question_text = models.TextField()
    question_type = models.CharField(
        max_length=20,
        choices=InterviewQuestionType.CHOICES,
        default=InterviewQuestionType.KNOWLEDGE,
    )
    question_format = models.CharField(
        max_length=20,
        choices=InterviewQuestionFormat.CHOICES,
        default=InterviewQuestionFormat.TEXT,
    )
    expected_steps = models.JSONField(default=list, blank=True)
    keywords = models.JSONField(default=list, blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    language = models.CharField(max_length=20, default="EN")
    scoring_type = models.CharField(max_length=50, blank=True)
    difficulty_score = models.PositiveSmallIntegerField(default=2)
    estimated_time_seconds = models.PositiveIntegerField(default=30)
    expected_answer_type = models.CharField(
        max_length=20,
        choices=ExpectedAnswerType.CHOICES,
        default=ExpectedAnswerType.STRUCTURED,
    )
    evaluation_tier = models.CharField(
        max_length=20,
        choices=InterviewEvaluationTier.CHOICES,
        default=InterviewEvaluationTier.FULL,
    )
    rubric_version = models.CharField(max_length=20, blank=True)
    question_set_version = models.CharField(max_length=20, blank=True)
    is_mandatory = models.BooleanField(default=True)
    follow_up_allowed = models.BooleanField(default=False)
    critical_question = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Question Template"
        verbose_name_plural = "Question Templates"
        indexes = [
            models.Index(fields=["role_code", "question_code", "language"]),
            models.Index(fields=["role_name", "role_code", "language", "is_active"]),
            models.Index(fields=["domain", "skill"]),
            models.Index(fields=["skill_tag", "evaluation_tier"]),
            models.Index(fields=["difficulty"]),
        ]
        ordering = ["role_name", "evaluation_tier", "sequence_number", "created_at"]

    def __str__(self):
        return f"{self.role_name} - {self.question_code or self.skill_tag} - {self.difficulty}"
