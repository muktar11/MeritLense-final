from django.http import FileResponse
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
    SessionPrivacyNoticeSerializer,
    SessionVerbalConfirmationSerializer,
)
from .services import AgreementService, CookieConsentService, open_signed_pdf


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

    def get(self, request):
        mismatches = AgreementService.version_mismatches_for_user(request.user)
        return Response({"mismatches": mismatches})


class CookieConsentCreateView(APIView):
    permission_classes = [AllowAny]

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
