from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from api.core.models import TimeStampedModel
from api.core.constants import AuditLogCategory, AuditLogAction, AuditLogSeverity
from api.accounts.models import User, Company

class AuditLog(TimeStampedModel):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs',
        help_text="User who performed the action"
    )
    
    user_email = models.EmailField(blank=True)
    user_name = models.CharField(max_length=255, blank=True)
    user_role = models.CharField(max_length=20, blank=True)
    
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs'
    )
    
    action = models.CharField(
        max_length=50,
        choices=AuditLogAction.CHOICES,
        db_index=True
    )
    
    category = models.CharField(
        max_length=50,
        choices=AuditLogCategory.CHOICES,
        db_index=True
    )
    
    severity = models.CharField(
        max_length=20,
        choices=AuditLogSeverity.CHOICES,
        default=AuditLogSeverity.INFO
    )
    
    resource_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    resource_id = models.PositiveIntegerField(null=True, blank=True)
    resource = GenericForeignKey('resource_type', 'resource_id')
    
    resource_name = models.CharField(max_length=255, blank=True)
    resource_type_name = models.CharField(max_length=100, blank=True)
    
    description = models.TextField()
    
    data = models.JSONField(default=dict, blank=True)
    
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    request_method = models.CharField(max_length=10, blank=True)
    request_path = models.CharField(max_length=500, blank=True)
    
    class Meta:
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['company', '-created_at']),
            models.Index(fields=['category', '-created_at']),
            models.Index(fields=['action', '-created_at']),
            models.Index(fields=['severity', '-created_at']),
            models.Index(fields=['resource_type', 'resource_id']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.created_at.strftime('%Y-%m-%d %H:%M')} - {self.user_email} - {self.action}"
    
    def save(self, *args, **kwargs):
        if self.user and not self.user_email:
            self.user_email = self.user.email
            self.user_name = self.user.get_full_name()
            self.user_role = self.user.role
        
        if self.resource and not self.resource_name:
            try:
                if hasattr(self.resource, 'name'):
                    self.resource_name = str(self.resource.name)
                elif hasattr(self.resource, 'title'):
                    self.resource_name = str(self.resource.title)
                elif hasattr(self.resource, 'email'):
                    self.resource_name = str(self.resource.email)
                else:
                    self.resource_name = str(self.resource)
                
                self.resource_type_name = self.resource._meta.verbose_name.title()
            except:
                pass
        
        super().save(*args, **kwargs)