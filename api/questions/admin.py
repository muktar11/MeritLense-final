from django.contrib import admin

from .models import QuestionTemplate


@admin.register(QuestionTemplate)
class QuestionTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "role_name",
        "role_code",
        "question_code",
        "skill_tag",
        "question_type",
        "question_format",
        "question_status",
        "difficulty",
        "language",
        "evaluation_tier",
        "is_active",
    )
    list_filter = (
        "role_name",
        "role_code",
        "question_status",
        "question_type",
        "question_format",
        "difficulty",
        "language",
        "evaluation_tier",
        "is_active",
        "is_mandatory",
        "critical_question",
    )
    search_fields = ("role_name", "role_code", "question_code", "domain", "skill_tag", "skill", "question_text")
