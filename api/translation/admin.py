from django.contrib import admin

from .models import (
    AIProcessingJob,
    CandidateResponseInterpretation,
    CandidateResponseTranslation,
    EvaluationInputArtifact,
)


@admin.register(CandidateResponseTranslation)
class CandidateResponseTranslationAdmin(admin.ModelAdmin):
    list_display = ("public_id", "response", "source_language", "target_language", "provider", "status", "idempotency_key", "translated_at")
    list_filter = ("status", "provider", "source_language", "target_language")
    search_fields = ("response__public_id", "provider", "translated_transcript", "original_transcript")


@admin.register(CandidateResponseInterpretation)
class CandidateResponseInterpretationAdmin(admin.ModelAdmin):
    list_display = ("public_id", "response", "provider", "model", "prompt_version", "prompt_hash", "status", "interpreted_at")
    list_filter = ("status", "provider", "prompt_version")
    search_fields = ("response__public_id", "provider", "model")


@admin.register(EvaluationInputArtifact)
class EvaluationInputArtifactAdmin(admin.ModelAdmin):
    list_display = ("public_id", "response", "competency_code", "source_interpretation_status", "requires_human_review", "prepared_at")
    list_filter = ("source_interpretation_status", "competency_code", "requires_human_review")
    search_fields = ("response__public_id", "competency_code")


@admin.register(AIProcessingJob)
class AIProcessingJobAdmin(admin.ModelAdmin):
    list_display = ("public_id", "response", "job_type", "status", "idempotency_key", "queue_name", "completed_at")
    list_filter = ("job_type", "status", "queue_name")
    search_fields = ("response__public_id", "idempotency_key", "queue_message_id")
