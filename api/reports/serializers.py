from rest_framework import serializers

from api.core.serializers import PublicIdModelSerializer
from api.reports.models import EvaluationReport


class EvaluationReportSerializer(PublicIdModelSerializer):
    evaluation_id = serializers.UUIDField(source="evaluation.public_id", read_only=True)
    session_id = serializers.UUIDField(source="session.public_id", read_only=True)
    candidate_id = serializers.UUIDField(source="candidate.public_id", read_only=True)
    generated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = EvaluationReport
        fields = [
            "id",
            "evaluation_id",
            "session_id",
            "candidate_id",
            "report_number",
            "report_version",
            "report_status",
            "overall_score",
            "max_score",
            "overall_percentage",
            "readiness_status",
            "requires_human_review",
            "scoring_rule_set_name",
            "scoring_rule_version",
            "report_payload",
            "competency_breakdown",
            "response_evidence_summary",
            "human_review_flags",
            "critical_failures",
            "generated_by",
            "generated_by_name",
            "generated_at",
            "last_regenerated_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_generated_by_name(self, obj):
        if obj.generated_by is None:
            return ""
        return obj.generated_by.get_full_name()


class EvaluationReportListSerializer(PublicIdModelSerializer):
    evaluation_id = serializers.UUIDField(source="evaluation.public_id", read_only=True)
    session_id = serializers.UUIDField(source="session.public_id", read_only=True)

    class Meta:
        model = EvaluationReport
        fields = [
            "id",
            "evaluation_id",
            "session_id",
            "report_number",
            "report_version",
            "report_status",
            "overall_percentage",
            "readiness_status",
            "requires_human_review",
            "scoring_rule_version",
            "generated_at",
            "last_regenerated_at",
        ]
        read_only_fields = fields
