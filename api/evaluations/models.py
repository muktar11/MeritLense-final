from django.utils import timezone
from django.db import models
from django.core.validators import MinValueValidator
from api.core.models import TimeStampedModel, SoftDeleteModel
from api.core.constants import EvaluationType, EvaluationStatus, CertificateStatus, JobRoles, Languages
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
        max_length=2, 
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
