from django.db import models

from api.core.models import TimeStampedModel


class AdminAlertConfiguration(TimeStampedModel):
    singleton_key = models.CharField(max_length=32, unique=True, default="default")
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Admin Alert Configuration"
        verbose_name_plural = "Admin Alert Configurations"

    def __str__(self):
        return f"Admin alert configuration ({self.singleton_key})"
