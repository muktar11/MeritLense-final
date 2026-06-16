from django.contrib import admin

from .models import InterviewConfiguration


@admin.register(InterviewConfiguration)
class InterviewConfigurationAdmin(admin.ModelAdmin):
    list_display = ("role_name", "language", "total_questions", "duration_minutes", "is_active")
    list_filter = ("language", "is_active", "enable_translation", "enable_integrity_checks")
    search_fields = ("role_name",)
