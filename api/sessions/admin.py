from django.contrib import admin

from .models import (
    CandidateResponse,
    IntegrityLog,
    InterviewSession,
    ObservedTaskDefinition,
    QuestionAudioArtifact,
    SessionObservedTask,
    SessionQuestion,
    TaskObservationResult,
)


@admin.register(InterviewSession)
class InterviewSessionAdmin(admin.ModelAdmin):
    list_display = (
        "public_id",
        "candidate",
        "status",
        "role_name",
        "current_question_index",
        "total_questions",
        "expires_at",
    )
    list_filter = ("status", "candidate_language", "tts_language_code", "stt_language_code")
    search_fields = ("candidate__first_name", "candidate__last_name", "role_name", "role_code", "access_token")


@admin.register(SessionQuestion)
class SessionQuestionAdmin(admin.ModelAdmin):
    list_display = ("public_id", "session", "question_order", "status", "skill", "difficulty")
    list_filter = ("status", "difficulty", "is_mandatory")
    search_fields = ("question_text", "skill", "domain")


@admin.register(CandidateResponse)
class CandidateResponseAdmin(admin.ModelAdmin):
    list_display = (
        "public_id",
        "session",
        "question",
        "response_type",
        "stt_status",
        "audio_mime_type",
        "duration_seconds",
        "created_at",
    )
    list_filter = ("response_type", "stt_status", "audio_mime_type")
    search_fields = ("transcript", "original_transcript", "stt_request_id")


@admin.register(QuestionAudioArtifact)
class QuestionAudioArtifactAdmin(admin.ModelAdmin):
    list_display = (
        "public_id",
        "session",
        "question",
        "provider",
        "voice_name",
        "language_code",
        "generated_at",
    )
    list_filter = ("provider", "language_code", "voice_name")
    search_fields = ("voice_name", "language_code", "question__question_text")


@admin.register(IntegrityLog)
class IntegrityLogAdmin(admin.ModelAdmin):
    list_display = ("public_id", "session", "candidate", "event_type", "severity", "detected_at")
    list_filter = ("severity", "event_type")
    search_fields = ("event_type", "candidate__first_name", "candidate__last_name")


@admin.register(ObservedTaskDefinition)
class ObservedTaskDefinitionAdmin(admin.ModelAdmin):
    list_display = ("task_code", "task_name", "role_code", "max_duration_seconds", "is_active")
    list_filter = ("is_active", "role_code")
    search_fields = ("task_code", "task_name", "role_code")


@admin.register(SessionObservedTask)
class SessionObservedTaskAdmin(admin.ModelAdmin):
    list_display = ("public_id", "session", "task_definition", "task_order", "status", "attempt_count")
    list_filter = ("status", "task_definition__role_code")
    search_fields = ("session__public_id", "task_definition__task_code", "task_definition__task_name")


@admin.register(TaskObservationResult)
class TaskObservationResultAdmin(admin.ModelAdmin):
    list_display = (
        "public_id",
        "session",
        "session_task",
        "status",
        "task_completed",
        "sequence_correct",
        "execution_time_seconds",
        "review_required",
        "generated_at",
    )
    list_filter = ("status", "review_required", "task_completed", "sequence_correct")
    search_fields = ("session__public_id", "candidate__email", "session_task__task_definition__task_code")
