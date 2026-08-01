from django.utils import timezone
from django.db import models
from django.db.models import Q
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from api.core.models import TimeStampedModel, SoftDeleteModel
from api.core.constants import (
    CertificateStatus,
    CoverageLevel,
    EvaluationStatus,
    EvaluationType,
    InterviewEvaluationTier,
    JobRoles,
    Languages,
    ReadinessStatus,
)
from api.candidates.models import Candidate
from api.accounts.models import User


class Evaluation(TimeStampedModel, SoftDeleteModel):
    session = models.OneToOneField(
        "interview_sessions.InterviewSession",
        on_delete=models.SET_NULL,
        related_name="linked_evaluation",
        null=True,
        blank=True,
        help_text="The interview session that produced this evaluation",
    )

    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name='evaluations',
        help_text="The candidate being evaluated"
    )
    
    evaluation_type = models.CharField(
        max_length=20,
        choices=EvaluationType.CHOICES,
        help_text="Type of evaluation (interview, technical test, etc.)"
    )
    
    status = models.CharField(
        max_length=20,
        choices=EvaluationStatus.CHOICES,
        default=EvaluationStatus.SCHEDULED,
        help_text="Current status of the evaluation"
    )
    
    scheduled_date = models.DateTimeField(
        help_text="Date and time when the evaluation is scheduled"
    )
    
    duration_minutes = models.PositiveIntegerField(
        validators=[MinValueValidator(15)],
        default=60,
        help_text="Duration of the evaluation in minutes"
    )
    
    candidate_first_name = models.CharField(max_length=150)
    candidate_last_name = models.CharField(max_length=150)
    candidate_email = models.EmailField()
    candidate_passport_id = models.CharField(max_length=50)
    candidate_job_role = models.CharField(max_length=2, choices=JobRoles.CHOICES)
    candidate_preferred_language = models.CharField(
        max_length=3,
        choices=Languages.CHOICES,
        default=Languages.ENGLISH
    )
    
    certificate_status = models.CharField(
        max_length=20,
        choices=CertificateStatus.CHOICES,
        default=CertificateStatus.NOT_ISSUED,
        help_text="Status of any certificate for this evaluation"
    )
    
    certificate_issued_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the certificate was issued (if applicable)"
    )
    
    certificate_url = models.URLField(
        null=True,
        blank=True,
        help_text="URL to the issued certificate"
    )
    
    last_evaluation_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Most recent evaluation date for this candidate (updated when evaluation is completed)"
    )
    
    score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Score achieved in the evaluation (if applicable)"
    )

    evaluation_tier = models.CharField(
        max_length=20,
        choices=InterviewEvaluationTier.CHOICES,
        default=InterviewEvaluationTier.FULL,
        help_text="Package-controlled evaluation tier for this session"
    )

    package_code = models.CharField(
        max_length=50,
        blank=True,
        help_text="Subscription package that initiated the evaluation"
    )

    coverage_level = models.CharField(
        max_length=20,
        choices=CoverageLevel.CHOICES,
        default=CoverageLevel.FULL,
        help_text="Role coverage level derived from package architecture"
    )

    readiness_indicator_enabled = models.BooleanField(
        default=True,
        help_text="Whether readiness output is enabled for this evaluation"
    )

    certificate_enabled = models.BooleanField(
        default=True,
        help_text="Whether certificate issuance is allowed for this evaluation"
    )

    readiness_status = models.CharField(
        max_length=20,
        choices=ReadinessStatus.CHOICES,
        default=ReadinessStatus.PENDING,
        help_text="Readiness decision after applying evaluation rules"
    )

    readiness_override_applied = models.BooleanField(
        default=False,
        help_text="Whether a rule-based readiness override has been applied"
    )

    readiness_override_reason = models.TextField(
        null=True,
        blank=True,
        help_text="Reason for readiness override, for example a failed critical question"
    )
    
    feedback = models.TextField(
        null=True,
        blank=True,
        help_text="Feedback on the evaluation"
    )
    
    meeting_link = models.URLField(
        null=True,
        blank=True,
        help_text="Link to video conference (Zoom, Google Meet, etc.)"
    )
    
    meeting_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Meeting ID or access code"
    )
    
    meeting_password = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Meeting password (if any)"
    )
    
    location = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Physical location for in-person evaluations"
    )
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_evaluations'
    )
    
    company = models.ForeignKey(
        'accounts.Company',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='evaluations'
    )
    
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the evaluation was completed"
    )
    
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the evaluation was cancelled"
    )
    
    cancellation_reason = models.TextField(
        null=True,
        blank=True,
        help_text="Reason for cancellation"
    )
    
    class Meta:
        verbose_name = "Evaluation"
        verbose_name_plural = "Evaluations"
        indexes = [
            models.Index(fields=['session']),
            models.Index(fields=['candidate']),
            models.Index(fields=['status']),
            models.Index(fields=['scheduled_date']),
            models.Index(fields=['created_by']),
            models.Index(fields=['company']),
            models.Index(fields=['evaluation_type']),
            models.Index(fields=['readiness_status']),
        ]
        ordering = ['-scheduled_date']
    
    def __str__(self):
        return f"{self.candidate_first_name} {self.candidate_last_name} - {self.get_evaluation_type_display()} - {self.scheduled_date.strftime('%Y-%m-%d %H:%M')}"
    
    def save(self, *args, **kwargs):
        if not self.pk and self.candidate:
            self.candidate_first_name = self.candidate.first_name
            self.candidate_last_name = self.candidate.last_name
            self.candidate_email = self.candidate.email
            self.candidate_passport_id = self.candidate.passport_id
            self.candidate_job_role = self.candidate.job_role
            self.candidate_preferred_language = self.candidate.preferred_language
            
            if self.candidate.company:
                self.company = self.candidate.company
        
        super().save(*args, **kwargs)
    
    def complete(self, score=None, feedback=None):
        self.status = EvaluationStatus.COMPLETED
        self.completed_at = timezone.now()
        if score is not None:
            self.score = score
        if feedback is not None:
            self.feedback = feedback
        
        self.candidate.last_evaluation_date = self.completed_at
        self.candidate.save(update_fields=['last_evaluation_date'])
        
        self.save()

    def cancel(self, reason=None):
        self.status = EvaluationStatus.CANCELLED
        self.cancelled_at = timezone.now()
        if reason:
            self.cancellation_reason = reason
        self.save()

    def reschedule(self, new_date):
        old_date = self.scheduled_date
        self.status = EvaluationStatus.RESCHEDULED
        self.scheduled_date = new_date
        self.save()
        return old_date

    def can_access(self, user):
        if user == self.created_by:
            return True

        if hasattr(user, 'managed_company') and user.managed_company == self.company:
            return True

        if user.role == 'B2B_TEAM_MEMBER' and user in self.candidate.shared_with.all():
            return True

        return False


class EvaluationReadinessDecisionRecord(TimeStampedModel):
    evaluation = models.OneToOneField(
        Evaluation,
        on_delete=models.CASCADE,
        related_name="readiness_legal_record",
    )
    session = models.ForeignKey(
        "interview_sessions.InterviewSession",
        on_delete=models.PROTECT,
        related_name="readiness_legal_records",
    )
    readiness_indicator = models.CharField(max_length=20)
    readiness_reason = models.TextField(blank=True)
    override_triggered = models.BooleanField(default=False)
    rule_engine_version = models.CharField(max_length=30)
    decided_at = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Evaluation Readiness Decision Record"
        verbose_name_plural = "Evaluation Readiness Decision Records"
        indexes = [
            models.Index(fields=["session", "decided_at"]),
            models.Index(fields=["rule_engine_version"]),
        ]
        ordering = ["-decided_at", "-created_at"]

    def __str__(self):
        return f"{self.evaluation_id} - {self.readiness_indicator} - {self.rule_engine_version}"

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Evaluation readiness decision records are immutable once generated.")
        if not self.session_id and self.evaluation.session_id:
            self.session = self.evaluation.session
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Evaluation readiness decision records cannot be deleted.")


class ScoringRuleSet(TimeStampedModel):
    name = models.CharField(max_length=150)
    version = models.CharField(max_length=40)
    description = models.TextField(blank=True)
    role_code = models.CharField(max_length=100, blank=True)
    role_name = models.CharField(max_length=100, blank=True)
    evaluation_tier = models.CharField(
        max_length=20,
        choices=InterviewEvaluationTier.CHOICES,
        default=InterviewEvaluationTier.FULL,
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_scoring_rule_sets",
    )
    company = models.ForeignKey(
        "accounts.Company",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scoring_rule_sets",
    )

    class Meta:
        verbose_name = "Scoring Rule Set"
        verbose_name_plural = "Scoring Rule Sets"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "role_code", "evaluation_tier", "version"],
                name="unique_scoring_ruleset_company_role_tier_version",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "role_code", "evaluation_tier", "is_active"]),
            models.Index(fields=["role_code", "evaluation_tier", "is_active"]),
            models.Index(fields=["version"]),
        ]
        ordering = ["role_code", "evaluation_tier", "-created_at"]

    def __str__(self):
        return f"{self.name} ({self.version})"

    @property
    def has_usage(self):
        return (
            self.response_results.exists()
            or self.competency_results.exists()
            or self.session_summaries.exists()
        )


class ScoringRule(TimeStampedModel):
    SCORING_METHOD_ALL_OR_NOTHING = "ALL_OR_NOTHING"
    SCORING_METHOD_WEIGHTED_MATCH = "WEIGHTED_INDICATOR_MATCH"
    SCORING_METHOD_REQUIRED_GATE = "REQUIRED_INDICATOR_GATE"
    SCORING_METHOD_CRITICAL_FAILURE = "CRITICAL_FAILURE_OVERRIDE"

    SCORING_METHOD_CHOICES = [
        (SCORING_METHOD_ALL_OR_NOTHING, "All Or Nothing"),
        (SCORING_METHOD_WEIGHTED_MATCH, "Weighted Indicator Match"),
        (SCORING_METHOD_REQUIRED_GATE, "Required Indicator Gate"),
        (SCORING_METHOD_CRITICAL_FAILURE, "Critical Failure Override"),
    ]

    rule_set = models.ForeignKey(
        ScoringRuleSet,
        on_delete=models.PROTECT,
        related_name="rules",
    )
    competency_code = models.CharField(max_length=150)
    competency_name = models.CharField(max_length=150, blank=True)
    question_template = models.ForeignKey(
        "questions.QuestionTemplate",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="scoring_rules",
    )
    question_code = models.CharField(max_length=50, blank=True)
    question_type = models.CharField(max_length=50, blank=True)
    expected_indicators = models.JSONField(default=list, blank=True)
    required_indicators = models.JSONField(default=list, blank=True)
    weighted_indicators = models.JSONField(default=dict, blank=True)
    critical_failure_indicators = models.JSONField(default=list, blank=True)
    risk_flags = models.JSONField(default=list, blank=True)
    max_score = models.DecimalField(max_digits=6, decimal_places=2, default=10)
    pass_threshold = models.DecimalField(max_digits=6, decimal_places=2, default=7)
    weight = models.DecimalField(max_digits=6, decimal_places=2, default=1)
    scoring_method = models.CharField(
        max_length=40,
        choices=SCORING_METHOD_CHOICES,
        default=SCORING_METHOD_WEIGHTED_MATCH,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Scoring Rule"
        verbose_name_plural = "Scoring Rules"
        constraints = [
            models.UniqueConstraint(
                fields=["rule_set", "question_template"],
                condition=Q(question_template__isnull=False),
                name="unique_scoring_rule_per_template",
            ),
        ]
        indexes = [
            models.Index(fields=["rule_set", "competency_code"]),
            models.Index(fields=["question_code"]),
        ]
        ordering = ["competency_code", "question_code", "created_at"]

    def __str__(self):
        return f"{self.rule_set.version} - {self.competency_code} - {self.question_code or 'generic'}"


class ResponseEvaluationResult(TimeStampedModel):
    evaluation = models.ForeignKey(
        Evaluation,
        on_delete=models.CASCADE,
        related_name="response_results",
    )
    session = models.ForeignKey(
        "interview_sessions.InterviewSession",
        on_delete=models.CASCADE,
        related_name="response_evaluation_results",
    )
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="response_evaluation_results",
    )
    response = models.ForeignKey(
        "interview_sessions.CandidateResponse",
        on_delete=models.CASCADE,
        related_name="evaluation_results",
    )
    question = models.ForeignKey(
        "interview_sessions.SessionQuestion",
        on_delete=models.CASCADE,
        related_name="evaluation_results",
    )
    rule_set = models.ForeignKey(
        ScoringRuleSet,
        on_delete=models.PROTECT,
        related_name="response_results",
    )
    rule = models.ForeignKey(
        ScoringRule,
        on_delete=models.PROTECT,
        related_name="response_results",
        null=True,
        blank=True,
    )
    competency_code = models.CharField(max_length=150, blank=True)
    competency_name = models.CharField(max_length=150, blank=True)
    score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    max_score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    percentage = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    passed_required_indicators = models.BooleanField(default=False)
    critical_failure = models.BooleanField(default=False)
    requires_human_review = models.BooleanField(default=False)
    observed_indicators = models.JSONField(default=list, blank=True)
    matched_indicators = models.JSONField(default=list, blank=True)
    missing_indicators = models.JSONField(default=list, blank=True)
    risk_flags = models.JSONField(default=list, blank=True)
    explanation = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    scored_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Response Evaluation Result"
        verbose_name_plural = "Response Evaluation Results"
        constraints = [
            models.UniqueConstraint(
                fields=["response", "rule_set"],
                name="unique_response_result_per_ruleset",
            ),
        ]
        indexes = [
            models.Index(fields=["evaluation", "competency_code"]),
            models.Index(fields=["session", "scored_at"]),
        ]
        ordering = ["question__question_order", "created_at"]


class CompetencyEvaluationResult(TimeStampedModel):
    STATUS_NOT_STARTED = "NOT_STARTED"
    STATUS_INCOMPLETE = "INCOMPLETE"
    STATUS_EVALUATED = "EVALUATED"
    STATUS_BELOW_THRESHOLD = "BELOW_THRESHOLD"
    STATUS_MEETS_THRESHOLD = "MEETS_THRESHOLD"

    STATUS_CHOICES = [
        (STATUS_NOT_STARTED, "Not Started"),
        (STATUS_INCOMPLETE, "Incomplete"),
        (STATUS_EVALUATED, "Evaluated"),
        (STATUS_BELOW_THRESHOLD, "Below Threshold"),
        (STATUS_MEETS_THRESHOLD, "Meets Threshold"),
    ]

    evaluation = models.ForeignKey(
        Evaluation,
        on_delete=models.CASCADE,
        related_name="competency_results",
    )
    session = models.ForeignKey(
        "interview_sessions.InterviewSession",
        on_delete=models.CASCADE,
        related_name="competency_evaluation_results",
    )
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="competency_evaluation_results",
    )
    rule_set = models.ForeignKey(
        ScoringRuleSet,
        on_delete=models.PROTECT,
        related_name="competency_results",
    )
    competency_code = models.CharField(max_length=150)
    competency_name = models.CharField(max_length=150, blank=True)
    total_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    max_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    percentage = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    pass_threshold = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_NOT_STARTED)
    response_count = models.PositiveIntegerField(default=0)
    completed_response_count = models.PositiveIntegerField(default=0)
    incomplete_response_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Competency Evaluation Result"
        verbose_name_plural = "Competency Evaluation Results"
        constraints = [
            models.UniqueConstraint(
                fields=["evaluation", "rule_set", "competency_code"],
                name="unique_competency_result_per_ruleset",
            ),
        ]
        indexes = [
            models.Index(fields=["evaluation", "status"]),
            models.Index(fields=["session", "competency_code"]),
        ]
        ordering = ["competency_code", "created_at"]


class SessionEvaluationSummary(TimeStampedModel):
    STATUS_PENDING = "PENDING"
    STATUS_PARTIALLY_EVALUATED = "PARTIALLY_EVALUATED"
    STATUS_EVALUATED = "EVALUATED"
    STATUS_EVALUATION_FAILED = "EVALUATION_FAILED"
    STATUS_REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PARTIALLY_EVALUATED, "Partially Evaluated"),
        (STATUS_EVALUATED, "Evaluated"),
        (STATUS_EVALUATION_FAILED, "Evaluation Failed"),
        (STATUS_REQUIRES_HUMAN_REVIEW, "Requires Human Review"),
    ]

    evaluation = models.ForeignKey(
        Evaluation,
        on_delete=models.CASCADE,
        related_name="session_summaries",
    )
    session = models.ForeignKey(
        "interview_sessions.InterviewSession",
        on_delete=models.CASCADE,
        related_name="evaluation_summaries",
    )
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="session_evaluation_summaries",
    )
    rule_set = models.ForeignKey(
        ScoringRuleSet,
        on_delete=models.PROTECT,
        related_name="session_summaries",
    )
    total_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    max_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    overall_percentage = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    evaluated_response_count = models.PositiveIntegerField(default=0)
    total_response_count = models.PositiveIntegerField(default=0)
    incomplete_response_count = models.PositiveIntegerField(default=0)
    competencies_summary = models.JSONField(default=list, blank=True)
    critical_failures = models.JSONField(default=list, blank=True)
    below_threshold_competencies = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    metadata = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Session Evaluation Summary"
        verbose_name_plural = "Session Evaluation Summaries"
        constraints = [
            models.UniqueConstraint(
                fields=["evaluation", "rule_set"],
                name="unique_session_summary_per_ruleset",
            ),
        ]
        indexes = [
            models.Index(fields=["evaluation", "status"]),
            models.Index(fields=["session", "generated_at"]),
        ]
        ordering = ["-generated_at", "-created_at"]
