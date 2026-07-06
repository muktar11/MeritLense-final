from rest_framework import serializers

from api.core.serializers import PublicIdModelSerializer

from .models import (
    CandidateResponseInterpretation,
    CandidateResponseTranslation,
    EvaluationInputArtifact,
)


class CandidateResponseTranslationSerializer(PublicIdModelSerializer):
    class Meta:
        model = CandidateResponseTranslation
        fields = [
            "id",
            "source_language",
            "target_language",
            "original_transcript",
            "translated_transcript",
            "provider",
            "provider_model",
            "idempotency_key",
            "status",
            "error_message",
            "metadata",
            "translated_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CandidateResponseInterpretationSerializer(PublicIdModelSerializer):
    class Meta:
        model = CandidateResponseInterpretation
        fields = [
            "id",
            "provider",
            "model",
            "prompt_version",
            "prompt_hash",
            "idempotency_key",
            "input_transcript_type",
            "input_language",
            "input_transcript",
            "structured_output",
            "normalized_indicators",
            "risk_flags",
            "confidence_score",
            "status",
            "error_message",
            "metadata",
            "interpreted_at",
            "legal_disclaimer",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class EvaluationInputArtifactSerializer(PublicIdModelSerializer):
    class Meta:
        model = EvaluationInputArtifact
        fields = [
            "id",
            "competency_code",
            "idempotency_key",
            "expected_indicators",
            "observed_indicators",
            "missing_indicators",
            "risk_flags",
            "language_notes",
            "source_interpretation_status",
            "requires_human_review",
            "review_reason",
            "legal_disclaimer",
            "metadata",
            "prepared_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
