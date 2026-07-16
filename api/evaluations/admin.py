from django.contrib import admin

from .models import (
    CompetencyEvaluationResult,
    Evaluation,
    EvaluationReadinessDecisionRecord,
    ResponseEvaluationResult,
    ScoringRule,
    ScoringRuleSet,
    SessionEvaluationSummary,
)


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


@admin.register(ScoringRuleSet)
class ScoringRuleSetAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "role_code", "evaluation_tier", "is_active", "created_at")
    list_filter = ("evaluation_tier", "is_active")
    search_fields = ("name", "version", "role_code", "role_name")


@admin.register(ScoringRule)
class ScoringRuleAdmin(admin.ModelAdmin):
    list_display = ("rule_set", "competency_code", "question_code", "scoring_method", "max_score", "pass_threshold", "is_active")
    list_filter = ("scoring_method", "is_active", "rule_set__evaluation_tier")
    search_fields = ("competency_code", "competency_name", "question_code", "rule_set__name", "rule_set__version")


@admin.register(ResponseEvaluationResult)
class ResponseEvaluationResultAdmin(admin.ModelAdmin):
    list_display = ("evaluation", "competency_code", "score", "percentage", "critical_failure", "requires_human_review", "scored_at")
    list_filter = ("critical_failure", "requires_human_review", "rule_set__version")
    search_fields = ("competency_code", "competency_name", "evaluation__candidate_email")


@admin.register(CompetencyEvaluationResult)
class CompetencyEvaluationResultAdmin(admin.ModelAdmin):
    list_display = ("evaluation", "competency_code", "percentage", "status", "response_count", "completed_response_count")
    list_filter = ("status", "rule_set__version")
    search_fields = ("competency_code", "competency_name", "evaluation__candidate_email")


@admin.register(SessionEvaluationSummary)
class SessionEvaluationSummaryAdmin(admin.ModelAdmin):
    list_display = ("evaluation", "overall_percentage", "status", "evaluated_response_count", "incomplete_response_count", "generated_at")
    list_filter = ("status", "rule_set__version")
    search_fields = ("evaluation__candidate_email", "rule_set__version")


@admin.register(EvaluationReadinessDecisionRecord)
class EvaluationReadinessDecisionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "evaluation",
        "session",
        "readiness_indicator",
        "override_triggered",
        "rule_engine_version",
        "decided_at",
    )
    list_filter = ("readiness_indicator", "override_triggered", "rule_engine_version")
    search_fields = ("evaluation__candidate_email", "session__public_id", "readiness_reason")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
