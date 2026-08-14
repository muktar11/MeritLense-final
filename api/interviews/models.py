from django.db import models

from api.core.constants import CoverageLevel, InterviewEvaluationTier, PackageAudience
from api.core.models import TimeStampedModel
from api.questions.skill_tags import normalize_skill_tag


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


class PackageSessionConfig(TimeStampedModel):
    package_code = models.CharField(max_length=50, unique=True)
    package_name = models.CharField(max_length=100)
    audience = models.CharField(
        max_length=10,
        choices=PackageAudience.CHOICES,
        default=PackageAudience.BOTH,
    )
    evaluation_tier = models.CharField(
        max_length=20,
        choices=InterviewEvaluationTier.CHOICES,
        default=InterviewEvaluationTier.SCREENING,
    )
    min_questions = models.PositiveIntegerField(default=5)
    max_questions = models.PositiveIntegerField(default=8)
    default_question_count = models.PositiveIntegerField(default=5)
    duration_minutes = models.PositiveIntegerField(default=15)
    task_observation_enabled = models.BooleanField(default=False)
    readiness_indicator_enabled = models.BooleanField(default=False)
    certificate_enabled = models.BooleanField(default=False)
    basic_report_enabled = models.BooleanField(default=True)
    analytics_enabled = models.BooleanField(default=False)
    api_access_enabled = models.BooleanField(default=False)
    video_introduction_enabled = models.BooleanField(default=False)
    behavioral_indicators_enabled = models.BooleanField(default=False)
    points_balance = models.PositiveIntegerField(null=True, blank=True)
    monthly_fee_display = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Package Session Config"
        verbose_name_plural = "Package Session Configs"
        ordering = ["audience", "package_name"]

    def __str__(self):
        return f"{self.package_name} ({self.package_code})"


class RolePackageCoverage(TimeStampedModel):
    role_name = models.CharField(max_length=100)
    role_code = models.CharField(max_length=100)
    package_code = models.CharField(max_length=50)
    package_name = models.CharField(max_length=100)
    audience = models.CharField(
        max_length=10,
        choices=PackageAudience.CHOICES,
        default=PackageAudience.BOTH,
    )
    coverage_level = models.CharField(
        max_length=20,
        choices=CoverageLevel.CHOICES,
        default=CoverageLevel.SCREENING,
    )
    evaluation_tier = models.CharField(
        max_length=20,
        choices=InterviewEvaluationTier.CHOICES,
        default=InterviewEvaluationTier.SCREENING,
    )
    readiness_indicator_enabled = models.BooleanField(default=False)
    certificate_enabled = models.BooleanField(default=False)
    video_introduction_enabled = models.BooleanField(default=False)
    behavioral_indicators_enabled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Role Package Coverage"
        verbose_name_plural = "Role Package Coverage"
        unique_together = [("role_code", "package_code")]
        ordering = ["role_name", "package_name"]

    def __str__(self):
        return f"{self.role_code} / {self.package_code} ({self.coverage_level})"


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

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        self.skill_tag = normalize_skill_tag(self.skill_tag, scoring_type=self.scoring_type)
        self.scoring_category = normalize_skill_tag(
            self.scoring_category,
            scoring_type=self.scoring_type,
            fallback=self.skill_tag,
        )
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"skill_tag", "scoring_category"}
        super().save(*args, **kwargs)
