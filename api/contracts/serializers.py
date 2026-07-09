from rest_framework import serializers

from api.core.public_ids import get_by_identifier
from api.core.serializers import PublicIdModelSerializer
from api.sessions.models import InterviewSession

from .models import Agreement, AgreementEvent, AgreementType, CookieConsent


class AgreementSerializer(PublicIdModelSerializer):
    class Meta:
        model = Agreement
        fields = [
            "id",
            "agreement_id",
            "agreement_type",
            "version",
            "status",
            "method",
            "signatory_name",
            "signed_at",
            "otp_reference",
            "otp_attempts",
            "pdf_path",
            "pdf_hash",
            "verification_url",
            "auth_checkbox_confirmed",
            "verbal_audio_path",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AgreementEventSerializer(PublicIdModelSerializer):
    class Meta:
        model = AgreementEvent
        fields = [
            "id",
            "event_type",
            "description",
            "ip_address",
            "user_agent",
            "data",
            "created_at",
        ]
        read_only_fields = fields


class CheckboxAcceptanceSerializer(serializers.Serializer):
    privacy_terms_accepted = serializers.BooleanField()
    ai_disclosure_accepted = serializers.BooleanField()


class InitiateAgreementSigningSerializer(serializers.Serializer):
    agreement_type = serializers.ChoiceField(choices=AgreementType.CHOICES)
    signatory_name = serializers.CharField(max_length=255)
    auth_checkbox_confirmed = serializers.BooleanField(required=False, default=False)
    session_id = serializers.CharField(required=False, allow_blank=True)
    token = serializers.CharField(required=False, allow_blank=True)
    company_stamp = serializers.FileField(required=False)

    def validate(self, attrs):
        session_id = attrs.get("session_id")
        if attrs["agreement_type"] == AgreementType.CANDIDATE_CONSENT:
            if not session_id:
                raise serializers.ValidationError({"session_id": "Candidate consent requires a session_id"})
            try:
                session = get_by_identifier(InterviewSession.objects.select_related("candidate", "created_by"), session_id)
            except InterviewSession.DoesNotExist:
                raise serializers.ValidationError({"session_id": "Interview session not found"})
            token = attrs.get("token", "")
            if not session.token_is_valid(token):
                raise serializers.ValidationError({"token": "A valid session token is required"})
            attrs["session"] = session
        return attrs


class ConfirmAgreementSigningSerializer(serializers.Serializer):
    agreement_id = serializers.CharField()
    otp_code = serializers.CharField(max_length=10)

    def validate_agreement_id(self, value):
        try:
            return get_by_identifier(Agreement.objects.all(), value)
        except Agreement.DoesNotExist:
            raise serializers.ValidationError("Agreement not found")


class ResendAgreementOtpSerializer(serializers.Serializer):
    agreement_id = serializers.CharField()

    def validate_agreement_id(self, value):
        try:
            return get_by_identifier(Agreement.objects.all(), value)
        except Agreement.DoesNotExist:
            raise serializers.ValidationError("Agreement not found")


class CookieConsentSerializer(PublicIdModelSerializer):
    class Meta:
        model = CookieConsent
        fields = [
            "id",
            "visitor_key",
            "categories_accepted",
            "expires_at",
            "created_at",
        ]
        read_only_fields = fields


class CookieConsentCreateSerializer(serializers.Serializer):
    visitor_key = serializers.CharField(required=False, allow_blank=True)
    categories_accepted = serializers.JSONField()


class SessionVerbalConfirmationSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    token = serializers.CharField()
    audio_file = serializers.FileField()

    def validate(self, attrs):
        try:
            session = get_by_identifier(InterviewSession.objects.select_related("candidate", "created_by"), attrs["session_id"])
        except InterviewSession.DoesNotExist:
            raise serializers.ValidationError({"session_id": "Interview session not found"})
        if not session.token_is_valid(attrs["token"]):
            raise serializers.ValidationError({"token": "A valid session token is required"})
        attrs["session"] = session
        return attrs


class SessionPrivacyNoticeSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    token = serializers.CharField()

    def validate(self, attrs):
        try:
            session = get_by_identifier(InterviewSession.objects.select_related("candidate", "created_by"), attrs["session_id"])
        except InterviewSession.DoesNotExist:
            raise serializers.ValidationError({"session_id": "Interview session not found"})
        if not session.token_is_valid(attrs["token"]):
            raise serializers.ValidationError({"token": "A valid session token is required"})
        attrs["session"] = session
        return attrs
