from rest_framework import serializers
from django.utils import timezone

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
from api.sessions.models import CandidateResponse, InterviewSession, QuestionAudioArtifact, SessionArtifact, SessionQuestion
from api.sessions.models import ObservedTaskDefinition, SessionObservedTask, TaskObservationResult


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


class ObservedTaskDefinitionSerializer(PublicIdModelSerializer):
    class Meta:
        model = ObservedTaskDefinition
        fields = [
            "id",
            "task_code",
            "task_name",
            "role_code",
            "description",
            "instruction_text",
            "expected_steps",
            "max_duration_seconds",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_expected_steps(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Expected steps must be a list")
        return value


class SessionObservedTaskSerializer(PublicIdModelSerializer):
    task_definition_id = serializers.UUIDField(source="task_definition.public_id", read_only=True)
    task_code = serializers.CharField(source="task_definition.task_code", read_only=True)
    task_name = serializers.CharField(source="task_definition.task_name", read_only=True)
    instruction_text = serializers.CharField(source="task_definition.instruction_text", read_only=True)
    expected_steps = serializers.ListField(source="task_definition.expected_steps", read_only=True)
    max_duration_seconds = serializers.IntegerField(source="task_definition.max_duration_seconds", read_only=True)

    class Meta:
        model = SessionObservedTask
        fields = [
            "id",
            "task_definition_id",
            "task_code",
            "task_name",
            "instruction_text",
            "expected_steps",
            "max_duration_seconds",
            "task_order",
            "status",
            "assigned_at",
            "started_at",
            "completed_at",
            "attempt_count",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TaskObservationResultSerializer(PublicIdModelSerializer):
    session_id = serializers.UUIDField(source="session.public_id", read_only=True)
    candidate_id = serializers.UUIDField(source="candidate.public_id", read_only=True)
    session_task_id = serializers.UUIDField(source="session_task.public_id", read_only=True)
    task_code = serializers.CharField(source="session_task.task_definition.task_code", read_only=True)
    task_name = serializers.CharField(source="session_task.task_definition.task_name", read_only=True)

    class Meta:
        model = TaskObservationResult
        fields = [
            "id",
            "session_id",
            "candidate_id",
            "session_task_id",
            "task_code",
            "task_name",
            "status",
            "task_completed",
            "sequence_correct",
            "execution_time_seconds",
            "review_required",
            "review_reason",
            "observed_steps",
            "missing_steps",
            "integrity_flags",
            "result_payload",
            "generated_by",
            "generated_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


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
    translation_artifact = serializers.SerializerMethodField()
    interpretation_artifact = serializers.SerializerMethodField()
    evaluation_input_artifact = serializers.SerializerMethodField()
    response_evaluation_result = serializers.SerializerMethodField()

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
            "translated_transcript",
            "translation_source_language",
            "translation_target_language",
            "translation_provider",
            "translation_model",
            "translation_status",
            "translation_error",
            "translation_metadata",
            "translated_at",
            "stt_provider",
            "stt_model",
            "stt_request_id",
            "stt_confidence",
            "stt_status",
            "stt_error_code",
            "stt_error_message",
            "stt_processed_at",
            "stt_metadata",
            "interpretation_status",
            "interpretation_error",
            "interpreted_at",
            "processing_status",
            "ai_processing_idempotency_key",
            "duration_seconds",
            "attempt_number",
            "metadata",
            "translation_artifact",
            "interpretation_artifact",
            "evaluation_input_artifact",
            "response_evaluation_result",
            "created_at",
        ]
        read_only_fields = fields

    def get_translation_artifact(self, obj):
        artifact = getattr(obj, "translation_artifact", None)
        if artifact is None:
            return None
        from api.translation.serializers import CandidateResponseTranslationSerializer

        return CandidateResponseTranslationSerializer(artifact).data

    def get_interpretation_artifact(self, obj):
        artifact = getattr(obj, "ai_interpretation", None)
        if artifact is None:
            return None
        from api.translation.serializers import CandidateResponseInterpretationSerializer

        return CandidateResponseInterpretationSerializer(artifact).data

    def get_evaluation_input_artifact(self, obj):
        artifact = getattr(obj, "evaluation_input_artifact", None)
        if artifact is None:
            return None
        from api.translation.serializers import EvaluationInputArtifactSerializer

        return EvaluationInputArtifactSerializer(artifact).data

    def get_response_evaluation_result(self, obj):
        result = obj.evaluation_results.select_related("rule_set").order_by("-created_at").first()
        if result is None:
            return None
        from api.evaluations.serializers import ResponseEvaluationResultSerializer

        return ResponseEvaluationResultSerializer(result).data


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
    linked_evaluation_id = serializers.SerializerMethodField()
    latest_scoring_summary = serializers.SerializerMethodField()
    latest_report = serializers.SerializerMethodField()
    observed_tasks = serializers.SerializerMethodField()
    task_results = serializers.SerializerMethodField()

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
            "scheduled_start_at",
            "started_at",
            "ended_at",
            "expires_at",
            "created_by",
            "access_token",
            "linked_evaluation_id",
            "latest_scoring_summary",
            "latest_report",
            "observed_tasks",
            "task_results",
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
            "cancelled_at",
            "cancellation_reason",
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

    def get_linked_evaluation_id(self, obj):
        evaluation = getattr(obj, "linked_evaluation", None)
        if evaluation is None:
            return None
        return str(evaluation.public_id)

    def _requesting_user_can_manage(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request is not None else None
        return obj.can_manage(user)

    def get_latest_scoring_summary(self, obj):
        # Scoring/readiness data must never reach a candidate holding only
        # their own session token — this field is staff-only even though
        # the endpoints that render this serializer also accept a token.
        if not self._requesting_user_can_manage(obj):
            return None
        summary = obj.evaluation_summaries.select_related("rule_set").first()
        if summary is None:
            return None
        from api.evaluations.serializers import SessionEvaluationSummarySerializer

        return SessionEvaluationSummarySerializer(summary).data

    def get_latest_report(self, obj):
        # Same staff-only restriction as get_latest_scoring_summary: the
        # legal evaluation report must not leak to a candidate via a plain
        # session token on GET/start/complete.
        if not self._requesting_user_can_manage(obj):
            return None
        evaluation = getattr(obj, "linked_evaluation", None)
        if evaluation is None:
            return None
        report = evaluation.reports.first()
        if report is None:
            return None
        from api.reports.serializers import EvaluationReportListSerializer

        return EvaluationReportListSerializer(report).data

    def get_observed_tasks(self, obj):
        tasks = obj.observed_tasks.select_related("task_definition").order_by("task_order")
        return SessionObservedTaskSerializer(tasks, many=True).data

    def get_task_results(self, obj):
        results = obj.task_observation_results.select_related("session_task", "session_task__task_definition").all()
        return TaskObservationResultSerializer(results, many=True).data


class InterviewSessionCreateSerializer(serializers.Serializer):
    candidate_id = serializers.CharField()
    config_id = serializers.CharField()
    package_code = serializers.CharField(required=False, allow_blank=True)
    scheduled_start_at = serializers.DateTimeField(required=False, allow_null=True)

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

        scheduled_start_at = attrs.get("scheduled_start_at")
        if scheduled_start_at and scheduled_start_at <= timezone.now():
            raise serializers.ValidationError({"scheduled_start_at": "Scheduled start time must be in the future"})

        return attrs


class SessionRescheduleSerializer(serializers.Serializer):
    scheduled_start_at = serializers.DateTimeField()

    def validate_scheduled_start_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("Scheduled start time must be in the future")
        return value


class SessionCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)


class SessionStartSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)


class SessionResponseSubmitSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    question_id = serializers.CharField()
    transcript = serializers.CharField()
    response_type = serializers.ChoiceField(choices=[("TEXT", "Text")], default="TEXT")
    text_response = serializers.CharField(required=False, allow_blank=True)
    duration_seconds = serializers.IntegerField(required=False, min_value=0, default=0)
    # Candidate-selected language for this specific answer (e.g. "ar-SA") -
    # overrides the session's fixed default so a candidate isn't locked to
    # whatever language they were assigned at session creation. Stored as
    # transcript_language, which is also what the translation pipeline uses
    # as its source language.
    language_code = serializers.CharField(required=False, allow_blank=True)

    def validate_question_id(self, value):
        session = self.context["session"]
        try:
            return get_by_identifier(session.questions.all(), value)
        except SessionQuestion.DoesNotExist:
            raise serializers.ValidationError("Question not found")


class SessionTokenSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)


class QuestionAudioRequestSerializer(SessionTokenSerializer):
    # Optional candidate-selected read-aloud language for this request only
    # (e.g. "ar-SA") - falls back to the session's own default when omitted.
    language_code = serializers.CharField(required=False, allow_blank=True)


class SessionTaskCompletionSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    event = serializers.CharField(required=False, allow_blank=True, default="TASK_COMPLETED")
    execution_time_seconds = serializers.IntegerField(min_value=0)
    observed_steps = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=True,
    )
    review_required = serializers.BooleanField(required=False, default=False)
    review_reason = serializers.CharField(required=False, allow_blank=True)
    integrity_flags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        default=list,
    )
    result_payload = serializers.DictField(required=False, default=dict)


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
    # Candidate-selected language this specific audio answer was recorded
    # in - used as the speech-to-text hint instead of the session's fixed
    # default, and stored as transcript_language.
    language_code = serializers.CharField(required=False, allow_blank=True)

    def validate_response_id(self, value):
        session = self.context["session"]
        try:
            return get_by_identifier(session.responses.all(), value)
        except CandidateResponse.DoesNotExist:
            raise serializers.ValidationError("Response not found")


class SessionConsentCaptureSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    signatory_name = serializers.CharField()


class SessionPrivacyAcknowledgementSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.DictField(required=False, default=dict)


class SessionDeviceCheckSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    passed = serializers.BooleanField(default=True)
    metadata = serializers.DictField(required=False, default=dict)


class SessionVerbalConfirmationSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    recording_file = serializers.FileField(required=False, allow_null=True)
    recording_path = serializers.CharField(required=False, allow_blank=True, default="")
    metadata = serializers.DictField(required=False, default=dict)

    def validate(self, attrs):
        if not attrs.get("recording_file") and not attrs.get("recording_path", "").strip():
            raise serializers.ValidationError(
                {"recording_file": "Provide either recording_file or recording_path."}
            )
        return attrs


class SessionIdentityVerificationSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    id_document_file = serializers.FileField(required=False, allow_null=True)
    selfie_image_file = serializers.FileField(required=False, allow_null=True)
    provider_result = serializers.JSONField(required=False)
    face_match_score = serializers.DecimalField(
        required=False,
        max_digits=5,
        decimal_places=2,
        min_value=0,
        max_value=100,
    )
    single_face_detected = serializers.BooleanField(required=False)
    liveness_passed = serializers.BooleanField(required=False, default=True)
    metadata = serializers.DictField(required=False, default=dict)


class SessionAccessPasswordVerifySerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField()


class SessionIntegrityEventSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    event_type = serializers.CharField(required=False, allow_blank=True)
    severity = serializers.CharField(required=False, default="INFO")
    frame_file = serializers.FileField(required=False, allow_null=True)
    provider_result = serializers.JSONField(required=False)
    single_face_detected = serializers.BooleanField(required=False)
    face_count = serializers.IntegerField(required=False, min_value=0)
    liveness_passed = serializers.BooleanField(required=False, default=True)
    auto_analyze = serializers.BooleanField(required=False, default=False)
    details = serializers.DictField(required=False, default=dict)

    def validate(self, attrs):
        if (
            not attrs.get("event_type", "").strip()
            and not attrs.get("auto_analyze")
            and not attrs.get("provider_result")
            and attrs.get("single_face_detected") is None
            and attrs.get("face_count") is None
            and attrs.get("liveness_passed") is True
        ):
            raise serializers.ValidationError(
                {"event_type": "Provide event_type or enable automatic integrity analysis input."}
            )
        return attrs


class SessionArtifactSerializer(PublicIdModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = SessionArtifact
        fields = [
            "id",
            "artifact_type",
            "file",
            "file_url",
            "mime_type",
            "file_size_bytes",
            "metadata",
            "uploaded_at",
        ]
        read_only_fields = fields

    def get_file_url(self, obj):
        request = self.context.get("request")
        if not obj.file:
            return ""
        if request is None:
            return obj.file.url
        return request.build_absolute_uri(obj.file.url)


class SessionPrecheckStatusSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    status = serializers.CharField()
    candidate_prechecks_complete = serializers.BooleanField()
    verification_status = serializers.CharField()
    identity_verified = serializers.BooleanField()
    face_match_score = serializers.CharField(allow_null=True)
    single_face_detected = serializers.BooleanField()
    candidate_consent_completed = serializers.BooleanField()
    privacy_notice_acknowledged = serializers.BooleanField()
    device_check_completed = serializers.BooleanField()
    verbal_confirmation_completed = serializers.BooleanField()
    latest_integrity_event = serializers.JSONField(allow_null=True)


class ResponseAIActionSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True)
    force = serializers.BooleanField(required=False, default=False)
    target_language = serializers.CharField(required=False, allow_blank=True)
    async_execution = serializers.BooleanField(required=False, default=False)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=120)


class ResponseAIProcessingStatusSerializer(serializers.Serializer):
    candidate_response_id = serializers.CharField(read_only=True)
    session_id = serializers.CharField(read_only=True)
    question_id = serializers.CharField(read_only=True)
    translation_status = serializers.CharField(read_only=True)
    interpretation_status = serializers.CharField(read_only=True)
    processing_status = serializers.CharField(read_only=True)
    ai_processing_idempotency_key = serializers.CharField(read_only=True)
    translation = serializers.SerializerMethodField()
    interpretation = serializers.SerializerMethodField()
    evaluation_input = serializers.SerializerMethodField()
    async_job = serializers.SerializerMethodField()

    def get_translation(self, obj):
        artifact = obj.get("translation")
        if artifact is None:
            return None
        from api.translation.serializers import CandidateResponseTranslationSerializer

        return CandidateResponseTranslationSerializer(artifact).data

    def get_interpretation(self, obj):
        artifact = obj.get("interpretation")
        if artifact is None:
            return None
        from api.translation.serializers import CandidateResponseInterpretationSerializer

        return CandidateResponseInterpretationSerializer(artifact).data

    def get_evaluation_input(self, obj):
        artifact = obj.get("evaluation_input")
        if artifact is None:
            return None
        from api.translation.serializers import EvaluationInputArtifactSerializer

        return EvaluationInputArtifactSerializer(artifact).data

    def get_async_job(self, obj):
        job = obj.get("async_job")
        if job is None:
            return None
        return {
            "id": str(job.public_id),
            "job_type": job.job_type,
            "status": job.status,
            "idempotency_key": job.idempotency_key,
            "queue_name": job.queue_name,
            "queue_message_id": job.queue_message_id,
            "error_message": job.error_message,
        }


class SessionAIProcessingSummarySerializer(serializers.Serializer):
    session_id = serializers.CharField(read_only=True)
    completed = serializers.BooleanField(read_only=True)
    responses = serializers.ListField(read_only=True)
