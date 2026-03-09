from django.db import models
from django.core.validators import FileExtensionValidator
from api.core.models import TimeStampedModel, SoftDeleteModel
from api.core.constants import candidateJobRoles, Languages, CandidateStatus
from api.accounts.models import User


class Candidate(TimeStampedModel, SoftDeleteModel):
    
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField()
    passport_id = models.CharField(max_length=50, unique=True)
    
    job_role = models.CharField(max_length=2, choices=candidateJobRoles.CHOICES)
    core_skills = models.TextField(help_text="Comma-separated skills")
    preferred_language = models.CharField(
        max_length=2, 
        choices=Languages.CHOICES, 
        default=Languages.ENGLISH
    )
    
    status = models.CharField(
        max_length=10,
        choices=CandidateStatus.CHOICES,
        default=CandidateStatus.ACTIVE
    )
    
    passport_document = models.FileField(
        upload_to='candidates/documents/passport/',
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])],
        help_text="Upload passport/ID document"
    )
    
    profile_photo = models.ImageField(
        upload_to='candidates/photos/',
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png'])],
        null=True,
        blank=True,
        help_text="Candidate profile photo"
    )
    
    last_evaluation_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Most recent evaluation date for this candidate (updated when evaluation is completed)"
    )
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_candidates'
    )
    
    company = models.ForeignKey(
        'accounts.Company',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='candidates'
    )
    
    shared_with = models.ManyToManyField(
        User,
        related_name='shared_candidates',
        blank=True,
        help_text="B2B team members who have access to this candidate"
    )
    
    class Meta:
        verbose_name = "Candidate"
        verbose_name_plural = "Candidates"
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['passport_id']),
            models.Index(fields=['status']),
            models.Index(fields=['created_by']),
            models.Index(fields=['company']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} - {self.email}"
    
    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
    
    def get_skills_list(self):
        if self.core_skills:
            return [skill.strip() for skill in self.core_skills.split(',')]
        return []
    
    def can_access(self, user):
        if user == self.created_by:
            return True
        
        if hasattr(user, 'managed_company') and user.managed_company == self.company:
            return True
        
        if user.role == 'B2B_TEAM_MEMBER' and user in self.shared_with.all():
            return True
        
        return False