from django.contrib import admin

from .models import Evaluation


@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):
    list_display = (
        "candidate_first_name",
        "candidate_last_name",
        "evaluation_type",
        "status",
        "score",
        "readiness_status",
        "readiness_override_applied",
        "scheduled_date",
    )
    list_filter = (
        "evaluation_type",
        "status",
        "readiness_status",
        "readiness_override_applied",
        "certificate_status",
    )
    search_fields = (
        "candidate_first_name",
        "candidate_last_name",
        "candidate_email",
        "candidate_passport_id",
    )
