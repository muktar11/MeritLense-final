from django.contrib import admin

from api.reports.models import EvaluationReport


@admin.register(EvaluationReport)
class EvaluationReportAdmin(admin.ModelAdmin):
    list_display = (
        "report_number",
        "evaluation",
        "report_status",
        "overall_percentage",
        "readiness_status",
        "readiness_indicator",
        "override_triggered",
        "rule_engine_version",
        "requires_human_review",
        "scoring_rule_version",
        "generated_at",
    )
    list_filter = (
        "report_status",
        "readiness_status",
        "readiness_indicator",
        "override_triggered",
        "requires_human_review",
        "scoring_rule_version",
        "rule_engine_version",
    )
    search_fields = (
        "report_number",
        "evaluation__candidate_email",
        "session__public_id",
        "candidate__email",
    )
    readonly_fields = (
        "report_number",
        "report_version",
        "evaluation",
        "session",
        "candidate",
        "overall_score",
        "max_score",
        "overall_percentage",
        "readiness_status",
        "readiness_indicator",
        "readiness_reason",
        "override_triggered",
        "rule_engine_version",
        "requires_human_review",
        "scoring_rule_set_name",
        "scoring_rule_version",
        "report_payload",
        "competency_breakdown",
        "response_evidence_summary",
        "human_review_flags",
        "critical_failures",
        "readiness_legal_record",
        "generated_by",
        "generated_at",
        "last_regenerated_at",
        "metadata",
        "created_at",
        "updated_at",
    )
