from django.utils import timezone
from rest_framework import serializers

from api.accounts.models import User
from api.core.serializers import PublicIdModelSerializer

from .models import ServiceIncident


class ServiceIncidentSerializer(PublicIdModelSerializer):
    cause_display = serializers.CharField(source="get_cause_display", read_only=True)
    session_id = serializers.UUIDField(source="session.public_id", read_only=True, allow_null=True)
    evaluation_id = serializers.UUIDField(source="evaluation.public_id", read_only=True, allow_null=True)
    company_name = serializers.CharField(source="company.name", read_only=True, allow_null=True)
    account_email = serializers.EmailField(source="account_user.email", read_only=True, allow_null=True)
    owner_email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    resolved_by_email = serializers.EmailField(source="resolved_by.email", read_only=True, allow_null=True)

    class Meta:
        model = ServiceIncident
        fields = [
            "id", "cause", "cause_display", "status", "affected_output", "summary", "details",
            "session_id", "evaluation_id", "company_name", "account_email",
            "owner_email", "next_action", "deadline", "occurrence_count", "last_seen_at", "notified_at",
            "resolved_at", "resolved_by_email", "resolution_note", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "cause", "cause_display", "affected_output", "summary", "details", "session_id",
            "evaluation_id", "company_name", "account_email", "occurrence_count", "last_seen_at",
            "notified_at", "resolved_at", "resolved_by_email", "created_at", "updated_at",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["owner_email"] = instance.owner.email if instance.owner else None
        return data

    def validate_owner_email(self, value):
        if not value:
            return None
        owner = User.objects.filter(email__iexact=value, is_active=True).first()
        if owner is None:
            raise serializers.ValidationError("No active user has this email.")
        return owner

    def validate(self, attrs):
        if attrs.get("status") == ServiceIncident.STATUS_RESOLVED and not (
            attrs.get("resolution_note") or getattr(self.instance, "resolution_note", "")
        ):
            raise serializers.ValidationError({"resolution_note": "Say how the incident was resolved."})
        return attrs

    def update(self, instance, validated_data):
        if "owner_email" in validated_data:
            instance.owner = validated_data.pop("owner_email")
        status = validated_data.get("status")
        if status == ServiceIncident.STATUS_RESOLVED and instance.status != ServiceIncident.STATUS_RESOLVED:
            instance.resolved_at = timezone.now()
            instance.resolved_by = self.context["request"].user
        elif status in ServiceIncident.ACTIVE_STATUSES:
            instance.resolved_at = None
            instance.resolved_by = None
        return super().update(instance, validated_data)
