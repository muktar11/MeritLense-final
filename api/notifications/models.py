from django.db import models

from api.core.models import TimeStampedModel


class Notification(TimeStampedModel):
    """In-app copy of an email sent to a registered user, shown in the
    dashboard bell. Created automatically for every outgoing email (see
    services.record_email_notifications), except security emails that
    carry codes, passwords or verification links."""

    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'read_at'])]

    def __str__(self):
        return f"{self.title} -> user {self.user_id}"
