from rest_framework import serializers

from api.candidates.models import Candidate
from api.core.public_ids import get_by_identifier
from api.core.serializers import PublicIdModelSerializer
from api.interviews.models import (
    InterviewConfiguration,
    InterviewRubric,
    PackageSessionConfig,
    RolePackageCoverage,
)
from api.questions.models import QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession, QuestionAudioArtifact, SessionQuestion


class InterviewConfigurationSerializer(PublicIdModelSerializer):
    class Meta:
        model = InterviewConfiguration
        fields = [
            "id",
            "role_name",
            "role_code",
            "language",
            "evaluation_tier",
            "duration_minutes",
            "total_questions",
            "allow_retries",
            "max_retries",
            "enable_translation",
            "enable_task_module",
            "enable_integrity_checks",
            "rubric_version",
            "question_set_version",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        total_questions = attrs.get("total_questions", getattr(self.instance, "total_questions", 0))
        max_retries = attrs.get("max_retries", getattr(self.instance, "max_retries", 0))
        allow_retries = attrs.get("allow_retries", getattr(self.instance, "allow_retries", False))

        if total_questions < 1:
            raise serializers.ValidationError({"total_questions": "Total questions must be at least 1"})
        if allow_retries and max_retries < 1:
            raise serializers.ValidationError({"max_retries": "Max retries must be at least 1 when retries are enabled"})
        return attrs


class QuestionTemplateSerializer(PublicIdModelSerializer):
    class Meta:
        model = QuestionTemplate
        fields = [
            "id",
            "role_name",
            "role_code",
            "question_code",
            "question_version",
            "question_status",
            "domain",
            "skill_tag",
            "skill_id",
            "skill",
            "sequence_number",
            "difficulty",
            "question_text",
            "question_type",
            "question_format",
            "expected_steps",
            "keywords",
            "weight",
            "language",
            "scoring_type",
            "difficulty_score",
            "estimated_time_seconds",
            "expected_answer_type",
            "evaluation_tier",
            "rubric_version",
            "question_set_version",
            "is_mandatory",
            "follow_up_allowed",
            "critical_question",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_expected_steps(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Expected steps must be a list")
        return value

    def validate_keywords(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Keywords must be a list")
        return value


class InterviewRubricSerializer(PublicIdModelSerializer):
    class Meta:
        model = InterviewRubric
        fields = [
            "id",
            "role_name",
            "role_code",
            "skill_tag",
            "scoring_category",
            "weight",
            "max_score",
            "scoring_type",
            "domain",
            "notes",
            "rubric_version",
            "question_set_version",
            "evaluation_criteria",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PackageSessionConfigSerializer(PublicIdModelSerializer):
    class Meta:
        model = PackageSessionConfig
        fields = [
            "id",
            "package_code",
            "package_name",
            "audience",
            "evaluation_tier",
            "min_questions",
            "max_questions",
            "default_question_count",
            "duration_minutes",
            "task_observation_enabled",
            "readiness_indicator_enabled",
            "certificate_enabled",
            "basic_report_enabled",
            "analytics_enabled",
            "api_access_enabled",
            "video_introduction_enabled",
            "behavioral_indicators_enabled",
            "points_balance",
            "monthly_fee_display",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class RolePackageCoverageSerializer(PublicIdModelSerializer):
    class Meta:
        model = RolePackageCoverage
        fields = [
            "id",
            "role_name",
            "role_code",
            "package_code",
            "package_name",
            "audience",
            "coverage_level",
            "evaluation_tier",
            "readiness_indicator_enabled",
            "certificate_enabled",
            "video_introduction_enabled",
            "behavioral_indicators_enabled",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SessionQuestionSerializer(PublicIdModelSerializer):
    class Meta:
        model = SessionQuestion
        fields = [
            "id",
            "question_text",
            "domain",
            "skill",
            "difficulty",
            "question_order",
            "status",
            "is_mandatory",
            "asked_at",
            "answered_at",
        ]
        read_only_fields = fields


class CandidateResponseSerializer(PublicIdModelSerializer):
    class Meta:
        model = CandidateResponse
        fields = [
            "id",
            "question",
            "response_type",
            "audio_file",
            "audio_url",
            "audio_mime_type",
            "audio_file_size_bytes",
            "audio_uploaded_at",
            "text_response",
            "transcript",
            "original_transcript",
            "transcript_language",
            "stt_provider",
            "stt_model",
            "stt_request_id",
            "stt_confidence",
            "stt_status",
            "stt_error_code",
            "stt_error_message",
            "stt_processed_at",
            "stt_metadata",
            "duration_seconds",
            "attempt_number",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class QuestionAudioArtifactSerializer(PublicIdModelSerializer):
    question_id = serializers.SerializerMethodField()

    class Meta:
        model = QuestionAudioArtifact
        fields = [
            "id",
            "question_id",
            "provider",
            "voice_name",
            "language_code",
            "audio_url",
            "mime_type",
            "file_size_bytes",
            "duration_estimate_seconds",
            "metadata",
            "generated_at",
        ]
        read_only_fields = fields

    def get_question_id(self, obj):
        return str(obj.question.public_id)


class InterviewSessionSerializer(PublicIdModelSerializer):
    candidate_name = serializers.SerializerMethodField()
    config_details = InterviewConfigurationSerializer(source="config", read_only=True)
    package_config_details = PackageSessionConfigSerializer(source="package_session_config", read_only=True)
    questions = SessionQuestionSerializer(many=True, read_only=True)
    progress_percent = serializers.SerializerMethodField()
    access_token = serializers.CharField(read_only=True)

    class Meta:
        model = InterviewSession
        fields = [
            "id",
            "candidate",
            "candidate_name",
            "organization",
            "config",
            "config_details",
            "package_session_config",
            "package_config_details",
            "status",
            "role_name",
            "role_code",
            "ui_language",
            "candidate_language",
            "tts_language_code",
            "stt_language_code",
            "translation_target",
            "current_question_index",
            "total_questions",
            "evaluation_tier",
            "package_code",
            "package_name",
            "coverage_level",
            "task_observation_enabled",
            "readiness_indicator_enabled",
            "certificate_enabled",
            "rubric_version",
            "question_set_version",
            "progress_percent",
            "started_at",
            "ended_at",
            "expires_at",
            "created_by",
            "access_token",
            "identity_verified",
            "face_match_score",
            "single_face_detected",
            "verification_status",
            "candidate_consent_agreement",
            "verbal_confirmation_path",
            "verbal_confirmation_recorded_at",
            "privacy_notice_acknowledged_at",
            "privacy_notice_ip_address",
            "device_check_completed_at",
            "questions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_candidate_name(self, obj):
        return obj.candidate.get_full_name()

    def get_progress_percent(self, obj):
        if not obj.total_questions:
            return 0
        answered = obj.questions.filter(status="ANSWERED").count()
        return int((answered / obj.total_questions) * 100)


class InterviewSessionCreateSerializer(serializers.Serializer):
    candidate_id = serializers.CharField()
    config_id = serializers.CharField()
    package_code = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        try:
            attrs["candidate"] = get_by_identifier(Candidate.objects.all(), attrs["candidate_id"])
        except Candidate.DoesNotExist:
            raise serializers.ValidationError({"candidate_id": "Candidate not found"})

        try:
            attrs["config"] = get_by_identifier(InterviewConfiguration.objects.filter(is_active=True), attrs["config_id"])
        except InterviewConfiguration.DoesNotExist:
            raise serializers.ValidationError({"config_id": "Interview configuration not found"})

        request = self.context["request"]
        if not attrs["candidate"].can_access(request.user):
            raise serializers.ValidationError({"candidate_id": "You do not have access to this candidate"})

        if attrs.get("package_code"):
            attrs["package_code"] = attrs["package_code"].strip().lower()

        return attrs


class SessionStartSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)


class SessionResponseSubmitSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    question_id = serializers.CharField()
    transcript = serializers.CharField()
    response_type = serializers.ChoiceField(choices=[("TEXT", "Text")], default="TEXT")
    text_response = serializers.CharField(required=False, allow_blank=True)
    duration_seconds = serializers.IntegerField(required=False, min_value=0, default=0)

    def validate_question_id(self, value):
        session = self.context["session"]
        try:
            return get_by_identifier(session.questions.all(), value)
        except SessionQuestion.DoesNotExist:
            raise serializers.ValidationError("Question not found")


class SessionTokenSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)


class SessionAudioUploadSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    question_id = serializers.CharField()
    audio_file = serializers.FileField()
    duration_seconds = serializers.IntegerField(min_value=0)

    def validate_question_id(self, value):
        session = self.context["session"]
        try:
            return get_by_identifier(session.questions.all(), value)
        except SessionQuestion.DoesNotExist:
            raise serializers.ValidationError("Question not found")


class SessionTranscriptionSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    response_id = serializers.CharField()

    def validate_response_id(self, value):
        session = self.context["session"]
        try:
            return get_by_identifier(session.responses.all(), value)
        except CandidateResponse.DoesNotExist:
            raise serializers.ValidationError("Response not found")
