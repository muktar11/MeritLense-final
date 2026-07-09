from rest_framework import serializers

from api.core.serializers import PublicIdModelSerializer
from api.core.constants import AgreementType, AgreementMethod

from .models import Agreement


class AgreementSerializer(PublicIdModelSerializer):
    agreement_type_display = serializers.CharField(source='get_agreement_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    signed_pdf_url = serializers.SerializerMethodField()

    class Meta:
        model = Agreement
        fields = [
            'id', 'agreement_type', 'agreement_type_display', 'version', 'method',
            'status', 'status_display', 'signatory_name', 'accepted_at', 'contract_id',
            'signed_pdf_url', 'pdf_hash', 'created_at',
        ]
        read_only_fields = fields

    def get_signed_pdf_url(self, obj):
        if not obj.signed_pdf:
            return None
        request = self.context.get('request')
        url = obj.signed_pdf.url
        return request.build_absolute_uri(url) if request else url


class AgreementAcceptSerializer(serializers.Serializer):
    """Checkbox-method acceptance for Privacy & Terms / AI Disclosure. The
    current version is looked up server-side (see CURRENT_VERSIONS) — the
    client only ever says *what* it's accepting, never *which version*, so
    there's no way for a stale client to accept the wrong version."""
    agreement_type = serializers.ChoiceField(choices=sorted(AgreementType.CHECKBOX_TYPES))
    accepted = serializers.BooleanField()

    def validate_accepted(self, value):
        if not value:
            raise serializers.ValidationError("Both agreements must be accepted to continue.")
        return value


class AgreementSignInitiateSerializer(serializers.Serializer):
    """Starts an OTP signing flow for one or more agreements signed together
    (e.g. B2B Agreement + DPA are signed with a single OTP). Versions are
    looked up server-side per type — B2B Agreement and DPA version
    independently (e.g. "Legal" vs "v1.2"), so the client must not pass a
    single shared version for the bundle."""
    agreement_types = serializers.ListField(
        child=serializers.ChoiceField(choices=sorted(AgreementType.OTP_TYPES)),
        min_length=1,
    )
    signatory_name = serializers.CharField(max_length=255)
    # Mandatory when B2B_AGREEMENT is in agreement_types (enforced in the
    # view, not here, since this checkbox doesn't apply to B2C/Candidate
    # signing) — "I confirm I am authorized to legally bind this organization."
    authorized_signatory_confirmed = serializers.BooleanField(required=False, default=False)

    def validate_agreement_types(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Duplicate agreement types in request.")
        return value


class AgreementSignConfirmSerializer(serializers.Serializer):
    otp_reference = serializers.CharField(max_length=100)
    code = serializers.CharField(max_length=10)


class AgreementSignResendSerializer(serializers.Serializer):
    otp_reference = serializers.CharField(max_length=100)
