from rest_framework import serializers
from django.utils import timezone
from django.conf import settings
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from .models import (
    CompetencyEvaluationResult,
    Evaluation,
    EvaluationReadinessDecisionRecord,
    ResponseEvaluationResult,
    ScoringRule,
    ScoringRuleSet,
    SessionEvaluationSummary,
)
from api.core.constants import CertificateStatus
from api.core.serializers import PublicIdModelSerializer
from api.candidates.serializers import CandidateSerializer
from api.core.constants import EvaluationType
from api.interviews.models import InterviewConfiguration
from api.sessions.models import InterviewSession
from api.sessions.services import InterviewSessionService


class EvaluationSerializer(PublicIdModelSerializer):
    candidate_details = CandidateSerializer(source='candidate', read_only=True)
    evaluation_type_display = serializers.CharField(source='get_evaluation_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    certificate_status_display = serializers.CharField(source='get_certificate_status_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    latest_session_summary = serializers.SerializerMethodField()
    readiness_legal_record = serializers.SerializerMethodField()
    latest_report = serializers.SerializerMethodField()
    
    class Meta:
        model = Evaluation
        fields = [
            'id', 'candidate', 'candidate_details',
            'candidate_first_name', 'candidate_last_name', 'candidate_email',
            'candidate_passport_id', 'candidate_job_role', 'candidate_preferred_language',
            'evaluation_type', 'evaluation_type_display',
            'status', 'status_display',
            'scheduled_date', 'duration_minutes',
            'evaluation_tier', 'package_code', 'coverage_level',
            'readiness_indicator_enabled', 'certificate_enabled',
            'certificate_status', 'certificate_status_display',
            'certificate_issued_at', 'certificate_url',
            'last_evaluation_date',
            'score', 'feedback',
            'latest_session_summary',
            'readiness_legal_record',
            'latest_report',
            'readiness_status', 'readiness_override_applied', 'readiness_override_reason',
            'meeting_link', 'meeting_id', 'meeting_password',
            'location',
            'created_by', 'created_by_name',
            'company',
            'completed_at', 'cancelled_at', 'cancellation_reason',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'candidate_first_name', 'candidate_last_name', 'candidate_email',
            'candidate_passport_id', 'candidate_job_role', 'candidate_preferred_language',
            'created_by', 'company', 'completed_at', 'cancelled_at',
            'evaluation_tier', 'package_code', 'coverage_level',
            'readiness_indicator_enabled', 'certificate_enabled',
            'readiness_status', 'readiness_override_applied', 'readiness_override_reason',
            'created_at', 'updated_at'
        ]
    
    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name()

    def get_latest_session_summary(self, obj):
        summary = obj.session_summaries.select_related("rule_set").first()
        if summary is None:
            return None
        return SessionEvaluationSummarySerializer(summary).data

    def get_readiness_legal_record(self, obj):
        try:
            record = obj.readiness_legal_record
        except EvaluationReadinessDecisionRecord.DoesNotExist:
            record = None
        if record is None:
            return None
        return EvaluationReadinessDecisionRecordSerializer(record).data

    def get_latest_report(self, obj):
        report = obj.reports.first()
        if report is None:
            return None
        from api.reports.serializers import EvaluationReportListSerializer

        return EvaluationReportListSerializer(report).data
    
    def validate_scheduled_date(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("Scheduled date must be in the future")
        return value


class EvaluationCreateSerializer(PublicIdModelSerializer):

    class Meta:
        model = Evaluation
        fields = [
            'candidate', 'evaluation_type', 'scheduled_date', 'duration_minutes',
            'meeting_link', 'meeting_id', 'meeting_password', 'location'
        ]
    
    def validate_candidate(self, value):
        request = self.context.get('request')
        user = request.user
        
        if not value.can_access(user):
            raise serializers.ValidationError("You don't have permission to schedule evaluations for this candidate")
        
        return value
    
    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['created_by'] = request.user
        
        candidate = validated_data.get('candidate')
        if candidate.company:
            validated_data['company'] = candidate.company

        if validated_data.get("evaluation_type") == EvaluationType.INTERVIEW:
            scheduled_date = validated_data["scheduled_date"]
            duration_minutes = validated_data.get("duration_minutes") or 60
            session = InterviewSessionService.create_session(
                candidate=candidate,
                config=self._resolve_interview_config(candidate),
                created_by=request.user,
                scheduled_start_at=scheduled_date,
            )
            session.expires_at = InterviewSession.build_expiry(duration_minutes, anchor_time=scheduled_date)
            session.token_expires_at = session.expires_at
            session.save(update_fields=["expires_at", "token_expires_at", "updated_at"])

            evaluation = session.linked_evaluation
            evaluation.scheduled_date = scheduled_date
            evaluation.duration_minutes = duration_minutes
            evaluation.meeting_link = self._build_interview_link(session, request)
            evaluation.meeting_id = str(session.public_id)
            evaluation.meeting_password = ""
            evaluation.location = ""
            evaluation.save(
                update_fields=[
                    "scheduled_date",
                    "duration_minutes",
                    "meeting_link",
                    "meeting_id",
                    "meeting_password",
                    "location",
                    "updated_at",
                ]
            )
            return evaluation

        return super().create(validated_data)

    def _resolve_interview_config(self, candidate):
        role_name = candidate.get_job_role_display()
        preferred_language = candidate.preferred_language or "EN"
        queryset = InterviewConfiguration.objects.filter(is_active=True)

        config = queryset.filter(
            role_name__iexact=role_name,
            language__iexact=preferred_language,
        ).order_by("evaluation_tier", "id").first()
        if config is None:
            config = queryset.filter(role_name__iexact=role_name).order_by("language", "evaluation_tier", "id").first()
        if config is None:
            raise serializers.ValidationError(
                {"candidate": f"No active AI interview configuration found for role '{role_name}'."}
            )
        return config

    def _build_interview_link(self, session, request):
        base_url = (settings.FRONTEND_URL or request.build_absolute_uri("/")).rstrip("/")
        path_template = getattr(settings, "INTERVIEW_FRONTEND_PATH_TEMPLATE", "/interview/{session_id}")
        path = path_template.format(session_id=session.public_id)
        if not path.startswith("/"):
            path = f"/{path}"

        split = urlsplit(f"{base_url}{path}")
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        query["token"] = session.access_token
        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


class EvaluationUpdateSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = Evaluation
        fields = [
            'evaluation_type', 'scheduled_date', 'duration_minutes',
            'meeting_link', 'meeting_id', 'meeting_password', 'location'
        ]
    
    def validate_scheduled_date(self, value):
        if value and value <= timezone.now():
            raise serializers.ValidationError("Scheduled date must be in the future")
        return value


class EvaluationCompleteSerializer(serializers.Serializer):
    score = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    feedback = serializers.CharField(required=False, allow_blank=True)
    certificate_status = serializers.ChoiceField(
        choices=CertificateStatus.CHOICES,
        required=False,
        default=CertificateStatus.NOT_ISSUED
    )
    certificate_url = serializers.URLField(required=False, allow_blank=True)


class EvaluationRescheduleSerializer(serializers.Serializer):
    new_date = serializers.DateTimeField()
    reason = serializers.CharField(required=False, allow_blank=True)
    
    def validate_new_date(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("New date must be in the future")
        return value


class EvaluationCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)


class EvaluationListSerializer(PublicIdModelSerializer):
    candidate_name = serializers.SerializerMethodField()
    evaluation_type_display = serializers.CharField(source='get_evaluation_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    latest_session_summary = serializers.SerializerMethodField()
    readiness_legal_record = serializers.SerializerMethodField()
    latest_report = serializers.SerializerMethodField()
    
    class Meta:
        model = Evaluation
        fields = [
            'id', 'candidate_name', 'evaluation_type', 'evaluation_type_display',
            'status', 'status_display', 'scheduled_date', 'duration_minutes',
            'score', 'latest_session_summary', 'readiness_legal_record', 'latest_report', 'readiness_status', 'readiness_override_applied', 'created_by', 'created_at'
        ]
    
    def get_candidate_name(self, obj):
        return f"{obj.candidate_first_name} {obj.candidate_last_name}"

    def get_latest_session_summary(self, obj):
        summary = obj.session_summaries.select_related("rule_set").first()
        if summary is None:
            return None
        return SessionEvaluationSummarySerializer(summary).data

    def get_readiness_legal_record(self, obj):
        try:
            record = obj.readiness_legal_record
        except EvaluationReadinessDecisionRecord.DoesNotExist:
            record = None
        if record is None:
            return None
        return EvaluationReadinessDecisionRecordSerializer(record).data

    def get_latest_report(self, obj):
        report = obj.reports.first()
        if report is None:
            return None
        from api.reports.serializers import EvaluationReportListSerializer

        return EvaluationReportListSerializer(report).data


class EvaluationReadinessDecisionRecordSerializer(PublicIdModelSerializer):
    session_id = serializers.UUIDField(source="session.public_id", read_only=True)
    evaluation_id = serializers.UUIDField(source="evaluation.public_id", read_only=True)

    class Meta:
        model = EvaluationReadinessDecisionRecord
        fields = [
            "id",
            "evaluation_id",
            "session_id",
            "readiness_indicator",
            "readiness_reason",
            "override_triggered",
            "rule_engine_version",
            "decided_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ScoringRuleSerializer(PublicIdModelSerializer):
    class Meta:
        model = ScoringRule
        fields = [
            "id",
            "rule_set",
            "competency_code",
            "competency_name",
            "question_template",
            "question_code",
            "question_type",
            "expected_indicators",
            "required_indicators",
            "weighted_indicators",
            "critical_failure_indicators",
            "risk_flags",
            "max_score",
            "pass_threshold",
            "weight",
            "scoring_method",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "rule_set", "created_at", "updated_at"]

    def validate(self, attrs):
        weighted_indicators = attrs.get("weighted_indicators", getattr(self.instance, "weighted_indicators", {})) or {}
        max_score = attrs.get("max_score", getattr(self.instance, "max_score", 0))
        if weighted_indicators and sum(float(value) for value in weighted_indicators.values()) <= 0:
            raise serializers.ValidationError({"weighted_indicators": "Weighted indicators must add up to more than zero"})
        if attrs.get("pass_threshold", getattr(self.instance, "pass_threshold", 0)) > max_score:
            raise serializers.ValidationError({"pass_threshold": "Pass threshold cannot exceed max score"})
        return attrs


class ScoringRuleSetSerializer(PublicIdModelSerializer):
    rules = ScoringRuleSerializer(many=True, required=False)
    has_usage = serializers.BooleanField(read_only=True)

    class Meta:
        model = ScoringRuleSet
        fields = [
            "id",
            "name",
            "version",
            "description",
            "company",
            "role_code",
            "role_name",
            "evaluation_tier",
            "is_active",
            "has_usage",
            "created_by",
            "rules",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company", "has_usage", "created_by", "created_at", "updated_at"]

    def create(self, validated_data):
        rules_data = validated_data.pop("rules", [])
        user = self.context["request"].user
        validated_data["created_by"] = user
        if hasattr(user, "company_profile") and user.company_profile:
            validated_data["company"] = user.company_profile.company
        elif hasattr(user, "managed_company") and user.managed_company:
            validated_data["company"] = user.managed_company
        rule_set = ScoringRuleSet.objects.create(**validated_data)
        for rule_data in rules_data:
            ScoringRule.objects.create(rule_set=rule_set, **rule_data)
        return rule_set

    def update(self, instance, validated_data):
        if instance.has_usage:
            raise serializers.ValidationError("This rule set has already been used and can no longer be modified")
        rules_data = validated_data.pop("rules", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if rules_data is not None:
            instance.rules.all().delete()
            for rule_data in rules_data:
                ScoringRule.objects.create(rule_set=instance, **rule_data)
        return instance


class ResponseEvaluationResultSerializer(PublicIdModelSerializer):
    rule_set_version = serializers.CharField(source="rule_set.version", read_only=True)
    question_id = serializers.UUIDField(source="question.public_id", read_only=True)
    response_id = serializers.UUIDField(source="response.public_id", read_only=True)

    class Meta:
        model = ResponseEvaluationResult
        fields = [
            "id",
            "response_id",
            "question_id",
            "competency_code",
            "competency_name",
            "score",
            "max_score",
            "percentage",
            "passed_required_indicators",
            "critical_failure",
            "requires_human_review",
            "observed_indicators",
            "matched_indicators",
            "missing_indicators",
            "risk_flags",
            "explanation",
            "rule_set_version",
            "metadata",
            "scored_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CompetencyEvaluationResultSerializer(PublicIdModelSerializer):
    rule_set_version = serializers.CharField(source="rule_set.version", read_only=True)

    class Meta:
        model = CompetencyEvaluationResult
        fields = [
            "id",
            "competency_code",
            "competency_name",
            "total_score",
            "max_score",
            "percentage",
            "pass_threshold",
            "status",
            "response_count",
            "completed_response_count",
            "incomplete_response_count",
            "rule_set_version",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SessionEvaluationSummarySerializer(PublicIdModelSerializer):
    rule_set_version = serializers.CharField(source="rule_set.version", read_only=True)

    class Meta:
        model = SessionEvaluationSummary
        fields = [
            "id",
            "total_score",
            "max_score",
            "overall_percentage",
            "evaluated_response_count",
            "total_response_count",
            "incomplete_response_count",
            "competencies_summary",
            "critical_failures",
            "below_threshold_competencies",
            "status",
            "rule_set_version",
            "metadata",
            "generated_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
