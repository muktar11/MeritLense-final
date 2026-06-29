from django.contrib import admin

from .models import CandidateResponse, IntegrityLog, InterviewSession, QuestionAudioArtifact, SessionQuestion


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
