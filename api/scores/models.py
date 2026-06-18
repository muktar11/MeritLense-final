from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from api.core.models import TimeStampedModel, SoftDeleteModel
from api.core.constants import ScoreArea
from api.candidates.models import Candidate
from api.evaluations.models import Evaluation
from api.accounts.models import User, Company


class ScoreCategory(TimeStampedModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    
    applicable_job_roles = models.CharField(
        max_length=255,
        help_text="Comma-separated job role codes this category applies to"
    )
    
    class Meta:
        verbose_name = "Score Category"
        verbose_name_plural = "Score Categories"
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def get_applicable_roles_list(self):
        if self.applicable_job_roles:
            return [role.strip() for role in self.applicable_job_roles.split(',')]
        return []


class CandidateScore(TimeStampedModel, SoftDeleteModel):
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name='scores'
    )
    
    evaluation = models.ForeignKey(
        Evaluation,
        on_delete=models.CASCADE,
        related_name='scores',
        null=True,
        blank=True,
        help_text="The evaluation this score is associated with (if any)"
    )
    
    area = models.CharField(
        max_length=50,
        choices=ScoreArea.CHOICES
    )
    
    score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    
    notes = models.TextField(
        blank=True,
        help_text="Additional notes about this score"
    )
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_scores'
    )
    
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='scores'
    )
    
    class Meta:
        verbose_name = "Candidate Score"
        verbose_name_plural = "Candidate Scores"
        unique_together = ['candidate', 'evaluation', 'area']
        indexes = [
            models.Index(fields=['candidate']),
            models.Index(fields=['evaluation']),
            models.Index(fields=['area']),
            models.Index(fields=['created_by']),
        ]
        ordering = ['candidate', 'area']
    
    def __str__(self):
        return f"{self.candidate.get_full_name()} - {self.get_area_display()}: {self.score}"
    
    def save(self, *args, **kwargs):
        if not self.company and self.candidate.company:
            self.company = self.candidate.company
        super().save(*args, **kwargs)
    
    def can_access(self, user):
        if user == self.created_by:
            return True
        
        if user == self.candidate.created_by:
            return True
        
        if hasattr(user, 'managed_company') and user.managed_company == self.company:
            return True
        
        if user.role == 'B2B_TEAM' and user in self.candidate.shared_with.all():
            return True
        
        return False


class ScoreSet(TimeStampedModel):
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name='score_sets'
    )
    
    evaluation = models.OneToOneField(
        Evaluation,
        on_delete=models.CASCADE,
        related_name='score_set',
        null=True,
        blank=True
    )
    
    average_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True
    )
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_score_sets'
    )
    
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='score_sets'
    )
    
    notes = models.TextField(blank=True)
    
    class Meta:
        verbose_name = "Score Set"
        verbose_name_plural = "Score Sets"
        indexes = [
            models.Index(fields=['candidate']),
            models.Index(fields=['evaluation']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Score Set for {self.candidate.get_full_name()} - Avg: {self.average_score}"
    
    def calculate_average(self):
        scores = CandidateScore.objects.filter(
            candidate=self.candidate,
            evaluation=self.evaluation
        )
        
        if scores.exists():
            total = sum([s.score for s in scores])
            self.average_score = total / len(scores)
        else:
            self.average_score = None
        
        self.save()
        
        if self.evaluation and self.average_score:
            self.evaluation.score = self.average_score
            self.evaluation.save(update_fields=['score'])

        if self.evaluation:
            from api.evaluations.rule_engine import EvaluationRuleEngine

            EvaluationRuleEngine.apply_readiness_rules(self.evaluation)
        
        return self.average_score
    
    def get_scores_dict(self):
        scores = CandidateScore.objects.filter(
            candidate=self.candidate,
            evaluation=self.evaluation
        )
        return {s.area: s.score for s in scores}
    
    def save(self, *args, **kwargs):
        if not self.company and self.candidate.company:
            self.company = self.candidate.company
        super().save(*args, **kwargs)
    
    def can_access(self, user):
        if user == self.created_by:
            return True
        
        if user == self.candidate.created_by:
            return True
        
        if hasattr(user, 'managed_company') and user.managed_company == self.company:
            return True
        
        if user.role == 'B2B_TEAM' and user in self.candidate.shared_with.all():
            return True
        
        return False
