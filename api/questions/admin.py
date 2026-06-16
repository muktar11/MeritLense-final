from django.contrib import admin

from .models import QuestionTemplate


@admin.register(QuestionTemplate)
class QuestionTemplateAdmin(admin.ModelAdmin):
    list_display = ("role_name", "skill", "difficulty", "language", "is_active", "is_mandatory")
    list_filter = ("role_name", "difficulty", "language", "is_active", "is_mandatory")
    search_fields = ("role_name", "domain", "skill", "question_text")
