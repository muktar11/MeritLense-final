from django.contrib import admin

from .models import InterviewConfiguration, InterviewRubric


@admin.register(InterviewConfiguration)
class InterviewConfigurationAdmin(admin.ModelAdmin):
    list_display = ("role_name", "role_code", "language", "evaluation_tier", "total_questions", "duration_minutes", "is_active")
    list_filter = ("language", "evaluation_tier", "is_active", "enable_translation", "enable_integrity_checks")
    search_fields = ("role_name", "role_code")


@admin.register(InterviewRubric)
class InterviewRubricAdmin(admin.ModelAdmin):
    list_display = ("role_name", "role_code", "skill_tag", "scoring_category", "max_score", "rubric_version", "is_active")
    list_filter = ("role_name", "role_code", "rubric_version", "is_active")
    search_fields = ("role_name", "role_code", "skill_tag", "scoring_category")
