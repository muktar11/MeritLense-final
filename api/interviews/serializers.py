from rest_framework import serializers

from api.candidates.models import Candidate
from api.core.public_ids import get_by_identifier
from api.core.serializers import PublicIdModelSerializer
from api.interviews.models import InterviewConfiguration
from api.questions.models import QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion


class InterviewConfigurationSerializer(PublicIdModelSerializer):
    class Meta:
        model = InterviewConfiguration
        fields = [
            "id",
            "role_name",
            "language",
            "duration_minutes",
            "total_questions",
            "allow_retries",
            "max_retries",
            "enable_translation",
            "enable_task_module",
            "enable_integrity_checks",
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
            "domain",
            "skill",
            "difficulty",
            "question_text",
            "expected_steps",
            "keywords",
            "weight",
            "language",
            "is_mandatory",
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
            "audio_url",
            "text_response",
            "transcript",
            "duration_seconds",
            "attempt_number",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class InterviewSessionSerializer(PublicIdModelSerializer):
    candidate_name = serializers.SerializerMethodField()
    config_details = InterviewConfigurationSerializer(source="config", read_only=True)
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
            "status",
            "role_name",
            "ui_language",
            "candidate_language",
            "tts_language_code",
            "stt_language_code",
            "translation_target",
            "current_question_index",
            "total_questions",
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
