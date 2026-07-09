from django.http import FileResponse
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.accounts.models import User
from api.core.constants import Roles
from api.core.public_ids import get_by_identifier

from .models import Agreement
from .serializers import (
    AgreementEventSerializer,
    AgreementSerializer,
    CheckboxAcceptanceSerializer,
    ConfirmAgreementSigningSerializer,
    CookieConsentCreateSerializer,
    CookieConsentSerializer,
    InitiateAgreementSigningSerializer,
    ResendAgreementOtpSerializer,
    SessionIdentityVerificationSerializer,
    SessionPrivacyNoticeSerializer,
    SessionVerbalConfirmationSerializer,
)
from .services import AgreementService, CookieConsentService, open_signed_pdf


error_response_serializer = inline_serializer(
    name="ContractsErrorResponse",
    fields={
        "detail": serializers.CharField(),
    },
)

agreement_initiate_response_serializer = inline_serializer(
    name="AgreementInitiateResponse",
    fields={
        "id": serializers.UUIDField(),
        "agreement_id": serializers.CharField(),
        "agreement_type": serializers.CharField(),
        "version": serializers.CharField(),
        "status": serializers.CharField(),
        "method": serializers.CharField(),
        "signatory_name": serializers.CharField(),
        "signed_at": serializers.DateTimeField(required=False, allow_null=True),
        "otp_reference": serializers.CharField(),
        "otp_attempts": serializers.IntegerField(),
        "pdf_path": serializers.CharField(required=False, allow_blank=True),
        "pdf_hash": serializers.CharField(required=False, allow_blank=True),
        "verification_url": serializers.CharField(required=False, allow_blank=True),
        "auth_checkbox_confirmed": serializers.BooleanField(),
        "verbal_audio_path": serializers.CharField(required=False, allow_blank=True),
        "created_at": serializers.DateTimeField(),
        "updated_at": serializers.DateTimeField(),
        "otp_expires_at": serializers.DateTimeField(required=False, allow_null=True),
        "resend_available_in_seconds": serializers.IntegerField(),
    },
)

agreement_status_response_serializer = inline_serializer(
    name="AgreementStatusResponse",
    fields={
        "user_id": serializers.CharField(),
        "agreements": serializers.ListField(child=serializers.JSONField()),
    },
)

agreement_audit_response_serializer = inline_serializer(
    name="AgreementAuditResponse",
    fields={
        "agreement": serializers.JSONField(),
        "events": serializers.ListField(child=serializers.JSONField()),
    },
)

agreement_verify_response_serializer = inline_serializer(
    name="AgreementVerifyResponse",
    fields={
        "agreement_id": serializers.CharField(),
        "type": serializers.CharField(),
        "version": serializers.CharField(),
        "signatory_name": serializers.CharField(),
        "signed_at": serializers.DateTimeField(required=False, allow_null=True),
        "status": serializers.CharField(),
        "sha256_hash": serializers.CharField(),
    },
)

agreement_version_check_response_serializer = inline_serializer(
    name="AgreementVersionCheckResponse",
    fields={
        "mismatches": serializers.ListField(child=serializers.JSONField()),
    },
)

session_step_response_serializer = inline_serializer(
    name="SessionStepResponse",
    fields={
        "session_id": serializers.CharField(),
        "identity_verified": serializers.BooleanField(required=False),
        "face_match_score": serializers.DecimalField(max_digits=5, decimal_places=2, required=False),
        "verification_status": serializers.CharField(required=False),
        "privacy_notice_acknowledged_at": serializers.DateTimeField(required=False),
        "device_check_completed_at": serializers.DateTimeField(required=False),
    },
)


class ContractPermissionMixin:
    def user_can_access_user_scope(self, request, target_user):
        if request.user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        return request.user == target_user

    def user_can_access_agreement(self, request, agreement):
        if request.user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        if agreement.user_id:
            return agreement.user_id == request.user.id
        return agreement.session and agreement.session.created_by_id == request.user.id


class AgreementCheckboxAcceptanceView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Accept checkbox agreements",
        description="Record acceptance of Privacy Policy & Terms and AI Disclosure for the current authenticated employer user.",
        request=CheckboxAcceptanceSerializer,
        responses={
            201: AgreementSerializer(many=True),
            400: error_response_serializer,
        },
        examples=[
            OpenApiExample(
                "Checkbox acceptance",
                value={
                    "privacy_terms_accepted": True,
                    "ai_disclosure_accepted": True,
                },
            )
        ],
    )
    def post(self, request):
        serializer = CheckboxAcceptanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            agreements = AgreementService.accept_registration_checkboxes(
                user=request.user,
                request=request,
                accepted={
                    "privacy_terms": serializer.validated_data["privacy_terms_accepted"],
                    "ai_disclosure": serializer.validated_data["ai_disclosure_accepted"],
                },
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(AgreementSerializer(agreements, many=True).data, status=status.HTTP_201_CREATED)


class AgreementInitiateSigningView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Initiate OTP agreement signing",
        description="Start OTP-based signing for B2B, B2C, or candidate consent agreements.",
        request=InitiateAgreementSigningSerializer,
        responses={
            200: agreement_initiate_response_serializer,
            201: agreement_initiate_response_serializer,
            400: error_response_serializer,
        },
        examples=[
            OpenApiExample(
                "B2C agreement",
                value={
                    "agreement_type": "b2c_agreement",
                    "signatory_name": "Auryetca Demo",
                },
            ),
            OpenApiExample(
                "B2B agreement",
                value={
                    "agreement_type": "b2b_agreement",
                    "signatory_name": "Authorized Signatory",
                    "auth_checkbox_confirmed": True,
                },
            ),
            OpenApiExample(
                "Candidate consent",
                value={
                    "agreement_type": "candidate_consent",
                    "signatory_name": "Candidate Name",
                    "session_id": "SESSION_PUBLIC_ID",
                    "token": "SESSION_ACCESS_TOKEN",
                },
            ),
        ],
    )
    def post(self, request):
        serializer = InitiateAgreementSigningSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agreement_type = serializer.validated_data["agreement_type"]
        session = serializer.validated_data.get("session")
        try:
            agreement, created = AgreementService.initiate_otp_signing(
                agreement_type=agreement_type,
                signatory_name=serializer.validated_data["signatory_name"],
                request=request,
                user=request.user if request.user.is_authenticated else None,
                session=session,
                auth_checkbox_confirmed=serializer.validated_data.get("auth_checkbox_confirmed", False),
                company_stamp=serializer.validated_data.get("company_stamp"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        data = AgreementSerializer(agreement).data
        data["otp_expires_at"] = agreement.otp_expires_at
        data["resend_available_in_seconds"] = 60
        return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class AgreementConfirmSigningView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Confirm OTP agreement signing",
        request=ConfirmAgreementSigningSerializer,
        responses={
            200: AgreementSerializer,
            400: error_response_serializer,
            403: error_response_serializer,
        },
        examples=[
            OpenApiExample(
                "Confirm OTP",
                value={
                    "agreement_id": "AGREEMENT_PUBLIC_ID",
                    "otp_code": "123456",
                },
            )
        ],
    )
    def post(self, request):
        serializer = ConfirmAgreementSigningSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agreement = serializer.validated_data["agreement_id"]
        if agreement.user_id and (not request.user.is_authenticated or not self._can_access_authenticated_agreement(request.user, agreement)):
            return Response({"detail": "You do not have access to this agreement"}, status=status.HTTP_403_FORBIDDEN)
        try:
            signed = AgreementService.confirm_otp(
                agreement=agreement,
                otp_code=serializer.validated_data["otp_code"],
                request=request,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(AgreementSerializer(signed).data)

    def _can_access_authenticated_agreement(self, user, agreement):
        return user.role in [Roles.ADMIN, Roles.SUPERADMIN] or agreement.user_id == user.id


class AgreementResendOtpView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Resend agreement OTP",
        request=ResendAgreementOtpSerializer,
        responses={
            200: AgreementSerializer,
            400: error_response_serializer,
            403: error_response_serializer,
        },
        examples=[
            OpenApiExample(
                "Resend OTP",
                value={
                    "agreement_id": "AGREEMENT_PUBLIC_ID",
                },
            )
        ],
    )
    def post(self, request):
        serializer = ResendAgreementOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agreement = serializer.validated_data["agreement_id"]
        if agreement.user_id and (not request.user.is_authenticated or not self._can_access_authenticated_agreement(request.user, agreement)):
            return Response({"detail": "You do not have access to this agreement"}, status=status.HTTP_403_FORBIDDEN)
        try:
            AgreementService.resend_otp(agreement=agreement, request=request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(AgreementSerializer(agreement).data)

    def _can_access_authenticated_agreement(self, user, agreement):
        return user.role in [Roles.ADMIN, Roles.SUPERADMIN] or agreement.user_id == user.id


class AgreementStatusView(ContractPermissionMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get agreement status for a user",
        responses={
            200: agreement_status_response_serializer,
            403: error_response_serializer,
            404: error_response_serializer,
        },
        parameters=[
            OpenApiParameter(name="user_id", location=OpenApiParameter.PATH, type=str),
        ],
    )
    def get(self, request, user_id):
        try:
            target_user = get_by_identifier(User.objects.all(), user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if not self.user_can_access_user_scope(request, target_user):
            return Response({"detail": "You do not have access to this user"}, status=status.HTTP_403_FORBIDDEN)
        payload = AgreementService.get_status_payload_for_user(target_user)
        return Response({"user_id": str(target_user.public_id), "agreements": payload})


class AgreementDownloadView(ContractPermissionMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Download signed agreement PDF",
        responses={
            200: OpenApiResponse(description="Binary PDF download"),
            403: error_response_serializer,
            404: error_response_serializer,
        },
        parameters=[
            OpenApiParameter(name="id", location=OpenApiParameter.PATH, type=str),
        ],
    )
    def get(self, request, id):
        try:
            agreement = get_by_identifier(Agreement.objects.all(), id)
        except Agreement.DoesNotExist:
            return Response({"detail": "Agreement not found"}, status=status.HTTP_404_NOT_FOUND)
        if not self.user_can_access_agreement(request, agreement):
            return Response({"detail": "You do not have access to this agreement"}, status=status.HTTP_403_FORBIDDEN)
        if not agreement.pdf_path:
            return Response({"detail": "Signed PDF is not available"}, status=status.HTTP_404_NOT_FOUND)
        file_handle = open_signed_pdf(agreement)
        response = FileResponse(file_handle, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{agreement.agreement_id}.pdf"'
        return response


class AgreementVerifyView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Public agreement verification",
        responses={
            200: agreement_verify_response_serializer,
            404: error_response_serializer,
        },
        parameters=[
            OpenApiParameter(name="id", location=OpenApiParameter.PATH, type=str),
        ],
    )
    def get(self, request, id):
        try:
            agreement = get_by_identifier(Agreement.objects.all(), id)
        except Agreement.DoesNotExist:
            return Response({"detail": "Agreement not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "agreement_id": agreement.agreement_id,
                "type": agreement.agreement_type,
                "version": agreement.version,
                "signatory_name": agreement.signatory_name,
                "signed_at": agreement.signed_at,
                "status": agreement.status,
                "sha256_hash": agreement.pdf_hash,
            }
        )


class AgreementAuditTrailView(ContractPermissionMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get agreement audit trail",
        responses={
            200: agreement_audit_response_serializer,
            403: error_response_serializer,
            404: error_response_serializer,
        },
        parameters=[
            OpenApiParameter(name="id", location=OpenApiParameter.PATH, type=str),
        ],
    )
    def get(self, request, id):
        try:
            agreement = get_by_identifier(Agreement.objects.prefetch_related("events"), id)
        except Agreement.DoesNotExist:
            return Response({"detail": "Agreement not found"}, status=status.HTTP_404_NOT_FOUND)
        if not self.user_can_access_agreement(request, agreement):
            return Response({"detail": "You do not have access to this agreement"}, status=status.HTTP_403_FORBIDDEN)
        return Response(
            {
                "agreement": AgreementSerializer(agreement).data,
                "events": AgreementEventSerializer(agreement.events.all(), many=True).data,
            }
        )


class AgreementVersionCheckView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Check agreement version mismatches",
        responses={
            200: agreement_version_check_response_serializer,
        },
    )
    def get(self, request):
        mismatches = AgreementService.version_mismatches_for_user(request.user)
        return Response({"mismatches": mismatches})


class CookieConsentCreateView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Get anonymous cookie consent by visitor key",
        responses={
            200: CookieConsentSerializer,
            400: error_response_serializer,
            404: error_response_serializer,
        },
        parameters=[
            OpenApiParameter(name="visitor_key", location=OpenApiParameter.QUERY, type=str, required=True),
        ],
    )
    def get(self, request):
        visitor_key = request.query_params.get("visitor_key", "").strip()
        if not visitor_key:
            return Response({"detail": "visitor_key query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
        consent = CookieConsentService.get_current_for_visitor(visitor_key)
        if consent is None:
            return Response({"detail": "Cookie consent not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(CookieConsentSerializer(consent).data)

    @extend_schema(
        summary="Record cookie consent",
        request=CookieConsentCreateSerializer,
        responses={
            201: CookieConsentSerializer,
            400: error_response_serializer,
        },
        examples=[
            OpenApiExample(
                "Cookie consent",
                value={
                    "visitor_key": "browser-visitor-1",
                    "categories_accepted": {
                        "strictly_necessary": True,
                        "functional": False,
                        "analytics": True,
                    },
                },
            )
        ],
    )
    def post(self, request):
        serializer = CookieConsentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consent = CookieConsentService.record_consent(
            user=request.user if request.user.is_authenticated else None,
            visitor_key=serializer.validated_data.get("visitor_key", ""),
            categories_accepted=serializer.validated_data["categories_accepted"],
            request=request,
        )
        return Response(CookieConsentSerializer(consent).data, status=status.HTTP_201_CREATED)


class CookieConsentStatusView(ContractPermissionMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get authenticated user's cookie consent",
        responses={
            200: CookieConsentSerializer,
            403: error_response_serializer,
            404: error_response_serializer,
        },
        parameters=[
            OpenApiParameter(name="user_id", location=OpenApiParameter.PATH, type=str),
        ],
    )
    def get(self, request, user_id):
        try:
            target_user = get_by_identifier(User.objects.all(), user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if not self.user_can_access_user_scope(request, target_user):
            return Response({"detail": "You do not have access to this user"}, status=status.HTTP_403_FORBIDDEN)
        consent = CookieConsentService.get_current_for_user(target_user)
        if consent is None:
            return Response({"detail": "Cookie consent not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(CookieConsentSerializer(consent).data)


class SessionVerbalConfirmationView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Upload candidate verbal confirmation audio",
        request=SessionVerbalConfirmationSerializer,
        responses={
            201: inline_serializer(
                name="SessionVerbalConfirmationResponse",
                fields={
                    "storage_key": serializers.CharField(),
                    "storage_url": serializers.CharField(),
                    "file_size_bytes": serializers.IntegerField(required=False, allow_null=True),
                    "filename": serializers.CharField(),
                },
            ),
            400: error_response_serializer,
        },
    )
    def post(self, request):
        serializer = SessionVerbalConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file_details = AgreementService.record_verbal_confirmation(
            session=serializer.validated_data["session"],
            uploaded_file=serializer.validated_data["audio_file"],
            request=request,
        )
        return Response(file_details, status=status.HTTP_201_CREATED)


class SessionPrivacyNoticeView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Acknowledge candidate privacy notice",
        request=SessionPrivacyNoticeSerializer,
        responses={
            200: session_step_response_serializer,
            400: error_response_serializer,
        },
    )
    def post(self, request):
        serializer = SessionPrivacyNoticeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = AgreementService.acknowledge_privacy_notice(
            session=serializer.validated_data["session"],
            request=request,
        )
        return Response(
            {
                "session_id": str(session.public_id),
                "privacy_notice_acknowledged_at": session.privacy_notice_acknowledged_at,
            }
        )


class SessionDeviceCheckView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Record candidate device check completion",
        request=SessionPrivacyNoticeSerializer,
        responses={
            200: session_step_response_serializer,
            400: error_response_serializer,
        },
    )
    def post(self, request):
        serializer = SessionPrivacyNoticeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = AgreementService.record_device_check(session=serializer.validated_data["session"])
        return Response(
            {
                "session_id": str(session.public_id),
                "device_check_completed_at": session.device_check_completed_at,
            }
        )


class SessionIdentityVerificationView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Record candidate identity verification result",
        request=SessionIdentityVerificationSerializer,
        responses={
            200: session_step_response_serializer,
            400: error_response_serializer,
        },
        examples=[
            OpenApiExample(
                "Identity verification success",
                value={
                    "session_id": "SESSION_PUBLIC_ID",
                    "token": "SESSION_ACCESS_TOKEN",
                    "face_match_score": "91.50",
                    "single_face_detected": True,
                },
            )
        ],
    )
    def post(self, request):
        serializer = SessionIdentityVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = AgreementService.record_identity_verification(
            session=serializer.validated_data["session"],
            face_match_score=serializer.validated_data["face_match_score"],
            single_face_detected=serializer.validated_data["single_face_detected"],
            request=request,
        )
        return Response(
            {
                "session_id": str(session.public_id),
                "identity_verified": session.identity_verified,
                "face_match_score": session.face_match_score,
                "verification_status": session.verification_status,
            }
        )
