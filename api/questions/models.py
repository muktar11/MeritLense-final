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
from .skill_tags import normalize_skill_fields


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

    def save(self, *args, **kwargs):
        normalized = normalize_skill_fields(
            skill_tag=self.skill_tag,
            skill=self.skill,
            skill_id=self.skill_id,
            scoring_type=self.scoring_type,
        )
        update_fields = kwargs.get("update_fields")
        self.skill_tag = normalized["skill_tag"]
        self.skill = normalized["skill"]
        self.skill_id = normalized["skill_id"]
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"skill_tag", "skill", "skill_id"}
        super().save(*args, **kwargs)


class IndicatorDefinition(TimeStampedModel):
    """One scoring indicator under a stable, immutable ID (e.g.
    ECG-TSK-001-MI-03) carrying its English source text and Arabic text
    together, so evidence is recorded against the ID rather than against
    language-specific free text (policy D-01). English is the locked bank
    source; Arabic carries its own approval status and only APPROVED text may
    ever be used outside Staging. Severity (D-04) and the mandatory/scored
    flag (D-02) stay empty until a subject-matter expert assigns them."""

    TYPE_MUST_INCLUDE = "MUST_INCLUDE"
    TYPE_NEGATIVE = "NEGATIVE"
    TYPE_CHOICES = [(TYPE_MUST_INCLUDE, "Must Include"), (TYPE_NEGATIVE, "Negative Indicator")]

    STATUS_DRAFT = "DRAFT"
    STATUS_APPROVED = "APPROVED"
    STATUS_CHOICES = [(STATUS_DRAFT, "Draft"), (STATUS_APPROVED, "Approved")]

    indicator_id = models.CharField(max_length=40, unique=True)
    question_code = models.CharField(max_length=50, db_index=True)
    bank_version = models.CharField(max_length=40)
    indicator_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    ordinal = models.PositiveSmallIntegerField()
    text_en = models.TextField(help_text="Locked English source text, exactly as in the approved bank.")
    text_ar = models.TextField(blank=True)
    text_ar_status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    text_ar_source = models.CharField(max_length=120, blank=True)
    severity = models.CharField(max_length=2, blank=True, help_text="S1/S2/S3 - empty until SME-assigned (D-04).")
    mandatory = models.BooleanField(null=True, blank=True, help_text="Mandatory vs scored - null until SME-assigned (D-02).")

    class Meta:
        ordering = ["question_code", "indicator_type", "ordinal"]
        constraints = [
            models.UniqueConstraint(
                fields=["question_code", "bank_version", "indicator_type", "ordinal"],
                name="unique_indicator_position_per_bank_version",
            ),
        ]

    def __str__(self):
        return self.indicator_id
