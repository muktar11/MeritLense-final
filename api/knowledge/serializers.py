from rest_framework import serializers

from api.core.serializers import PublicIdModelSerializer

from .models import KnowledgeEntry


class KnowledgeEntryAdminSerializer(PublicIdModelSerializer):
    """Full-fidelity serializer for the authenticated admin management
    endpoint only - includes every classification field. Never reused for
    the public read endpoint (see views.PublicKnowledgeBaseView, which
    builds its own minimal, hand-picked response instead)."""

    supersedes = serializers.UUIDField(source="supersedes.public_id", read_only=True, allow_null=True)

    class Meta:
        model = KnowledgeEntry
        fields = [
            "id",
            "question_id",
            "category",
            "category_ar",
            "category_id",
            "category_order",
            "sequence",
            "question_en",
            "question_ar",
            "short_answer_en",
            "short_answer_ar",
            "detailed_answer_en",
            "detailed_answer_ar",
            "audience",
            "status",
            "version",
            "owner",
            "visibility",
            "content_classification",
            "sensitive_content",
            "ai_retrieval_allowed",
            "last_security_review",
            "is_current",
            "supersedes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_current", "supersedes", "created_at", "updated_at"]
        # question_id is writable here (needed for create) but the service
        # layer (KnowledgeContentService.create_new_version) always forces
        # it back to the previous version's value on an update, regardless
        # of what a client submits - it identifies which question a row is
        # a version of and must never change out from under an edit.
