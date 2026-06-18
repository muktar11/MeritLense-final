from django.contrib import admin

from .models import QuestionTemplate


@admin.register(QuestionTemplate)
class QuestionTemplateAdmin(admin.ModelAdmin):
    list_display = ("role_name", "role_code", "skill_tag", "question_type", "difficulty", "language", "evaluation_tier", "is_active")
    list_filter = ("role_name", "role_code", "difficulty", "language", "evaluation_tier", "is_active", "is_mandatory")
    search_fields = ("role_name", "role_code", "domain", "skill_tag", "skill", "question_text")
