import secrets

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.core.constants import (
    AgreementType, AgreementMethod, AgreementStatus,
    AuditLogAction, AuditLogCategory, Roles,
)
from api.core.public_ids import get_by_identifier
from api.audit.models import AuditLog
from api.audit.serializers import AuditLogSerializer
from api.audit.services import AuditLogService

from .constants import CURRENT_VERSIONS
from .models import Agreement
from .otp_service import OTPService
from .pdf_service import render_agreement_pdf, render_preview_html, generate_contract_id, compute_pdf_hash
from .serializers import (
    AgreementSerializer,
    AgreementAcceptSerializer,
    AgreementSignInitiateSerializer,
    AgreementSignConfirmSerializer,
    AgreementSignResendSerializer,
)

DOWNLOADABLE_TYPES = {AgreementType.B2B_AGREEMENT, AgreementType.DPA, AgreementType.B2C_AGREEMENT}

# Company-scoped agreement types are shared across the whole company rather
# than tracked per signing user (any authorized rep signs on the company's behalf).
COMPANY_SCOPED_TYPES = {AgreementType.B2B_AGREEMENT, AgreementType.DPA}


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _user_company(user):
    return getattr(user, 'managed_company', None)


def _mask_email(email):
    local, _, domain = email.partition('@')
    if not domain:
        return "***"
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


class AgreementAcceptView(APIView):
    """POST /agreements/accept — checkbox-method acceptance (Privacy & Terms, AI Disclosure)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AgreementAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        version = CURRENT_VERSIONS[data['agreement_type']]

        Agreement.objects.filter(
            user=request.user,
            agreement_type=data['agreement_type'],
            status=AgreementStatus.SIGNED,
        ).exclude(version=version).update(status=AgreementStatus.SUPERSEDED)

        agreement, _created = Agreement.objects.update_or_create(
            user=request.user,
            agreement_type=data['agreement_type'],
            version=version,
            defaults={
                'method': AgreementMethod.CHECKBOX,
                'status': AgreementStatus.SIGNED,
                'accepted_at': timezone.now(),
                'ip_address': _client_ip(request),
                'user_agent': request.META.get('HTTP_USER_AGENT', ''),
            },
        )

        AuditLogService.log(
            user=request.user,
            action=AuditLogAction.AGREEMENT_ACCEPTED,
            category=AuditLogCategory.DOCUMENT,
            description=f"Accepted {data['agreement_type']} v{version} via checkbox",
            resource=agreement,
            data={'agreement_type': data['agreement_type'], 'version': version},
            request=request,
        )

        return Response(AgreementSerializer(agreement, context={'request': request}).data)


class AgreementSignInitiateView(APIView):
    """POST /agreements/sign/initiate — dispatch OTP for one or more agreements
    signed together (e.g. B2B Agreement + DPA)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AgreementSignInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if AgreementType.B2B_AGREEMENT in data['agreement_types'] and not data.get('authorized_signatory_confirmed'):
            return Response(
                {'error': 'You must confirm you are authorized to legally bind this organization.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        company = _user_company(request.user)
        email = request.user.email

        otp_service = OTPService()
        code, code_hash, expires_at = otp_service.issue(email)
        otp_reference = secrets.token_urlsafe(12)
        now = timezone.now()

        agreements = []
        for agreement_type in data['agreement_types']:
            version = CURRENT_VERSIONS[agreement_type]
            Agreement.objects.filter(
                user=request.user,
                agreement_type=agreement_type,
                status=AgreementStatus.SIGNED,
            ).exclude(version=version).update(status=AgreementStatus.SUPERSEDED)

            agreement, _created = Agreement.objects.update_or_create(
                user=request.user,
                agreement_type=agreement_type,
                version=version,
                status=AgreementStatus.PENDING,
                defaults={
                    'company': company if agreement_type in COMPANY_SCOPED_TYPES else None,
                    'method': AgreementMethod.OTP_SIGNATURE,
                    'signatory_name': data['signatory_name'],
                    'auth_checkbox_confirmed': bool(data.get('authorized_signatory_confirmed')),
                    'otp_code_hash': code_hash,
                    'otp_expires_at': expires_at,
                    'otp_attempts': 0,
                    'otp_resend_count': 0,
                    'otp_last_sent_at': now,
                    'otp_reference': otp_reference,
                },
            )
            agreements.append(agreement)

        sent = otp_service.send(email, code)

        for agreement in agreements:
            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.AGREEMENT_SIGN_INITIATED,
                category=AuditLogCategory.DOCUMENT,
                description=f"Signing initiated for {agreement.agreement_type} v{agreement.version}",
                resource=agreement,
                data={'agreement_types': data['agreement_types'], 'email_sent': sent},
                request=request,
            )

        masked = _mask_email(email)
        return Response({
            'otp_reference': otp_reference,
            'sent_to': masked,
            'email_dispatched': sent,
            'expires_at': expires_at,
        })


class AgreementSignResendView(APIView):
    """POST /agreements/sign/resend — resend the OTP code for an in-progress
    signing session. Rate-limited: 60-second cooldown, max 5 resends, after
    which the session is locked and the user must restart from review."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AgreementSignResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp_reference = serializer.validated_data['otp_reference']

        agreements = list(Agreement.objects.filter(
            user=request.user,
            otp_reference=otp_reference,
            status=AgreementStatus.PENDING,
        ))
        if not agreements:
            return Response(
                {'error': 'No pending signing request found for this reference.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        lead = agreements[0]
        if lead.otp_resend_count >= settings.AGREEMENT_OTP_MAX_RESENDS:
            return Response(
                {'error': 'Maximum resend attempts reached. Please start over.', 'locked': True},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if lead.otp_last_sent_at:
            elapsed = (timezone.now() - lead.otp_last_sent_at).total_seconds()
            remaining = settings.AGREEMENT_OTP_RESEND_COOLDOWN_SECONDS - elapsed
            if remaining > 0:
                return Response(
                    {'error': f'Please wait {int(remaining)}s before requesting a new code.'},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

        otp_service = OTPService()
        email = request.user.email
        code, code_hash, expires_at = otp_service.issue(email)
        now = timezone.now()

        for agreement in agreements:
            agreement.otp_code_hash = code_hash
            agreement.otp_expires_at = expires_at
            agreement.otp_attempts = 0
            agreement.otp_resend_count += 1
            agreement.otp_last_sent_at = now
            agreement.save(update_fields=[
                'otp_code_hash', 'otp_expires_at', 'otp_attempts',
                'otp_resend_count', 'otp_last_sent_at',
            ])

        sent = otp_service.send(email, code)

        for agreement in agreements:
            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.AGREEMENT_SIGN_INITIATED,
                category=AuditLogCategory.DOCUMENT,
                description=f"Signing code resent for {agreement.agreement_type} v{agreement.version} "
                             f"(attempt {lead.otp_resend_count}/{settings.AGREEMENT_OTP_MAX_RESENDS})",
                resource=agreement,
                data={'otp_reference': otp_reference, 'email_sent': sent},
                request=request,
            )

        return Response({
            'otp_reference': otp_reference,
            'sent_to': _mask_email(email),
            'email_dispatched': sent,
            'expires_at': expires_at,
            'resends_remaining': settings.AGREEMENT_OTP_MAX_RESENDS - lead.otp_resend_count,
        })


class AgreementSignConfirmView(APIView):
    """POST /agreements/sign/confirm — validate OTP, generate signed PDF(s), audit log."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AgreementSignConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        agreements = list(Agreement.objects.filter(
            user=request.user,
            otp_reference=data['otp_reference'],
            status=AgreementStatus.PENDING,
        ))
        if not agreements:
            return Response(
                {'error': 'No pending signing request found for this reference.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        otp_service = OTPService()
        ok, error = otp_service.verify(agreements[0], data['code'])
        if not ok:
            locked = agreements[0].otp_attempts >= settings.AGREEMENT_OTP_MAX_ATTEMPTS
            for agreement in agreements:
                AuditLogService.log(
                    user=request.user,
                    action=AuditLogAction.AGREEMENT_SIGN_FAILED,
                    category=AuditLogCategory.DOCUMENT,
                    description=f"OTP confirmation failed for {agreement.agreement_type}: {error}",
                    resource=agreement,
                    data={'otp_reference': data['otp_reference'], 'locked': locked},
                    request=request,
                )
            return Response({'error': error, 'locked': locked}, status=status.HTTP_400_BAD_REQUEST)

        company = _user_company(request.user)
        now = timezone.now()
        ip_address = _client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        signed = []

        for agreement in agreements:
            agreement.status = AgreementStatus.SIGNED
            agreement.accepted_at = now
            agreement.ip_address = ip_address
            agreement.user_agent = user_agent
            agreement.contract_id = generate_contract_id()
            agreement.otp_code_hash = ''
            agreement.otp_expires_at = None

            try:
                render_agreement_pdf(agreement, company=company, user=request.user, request=request)
                agreement.pdf_hash = compute_pdf_hash(agreement.signed_pdf)
            except Exception as exc:
                AuditLogService.log(
                    user=request.user,
                    action=AuditLogAction.AGREEMENT_SIGN_FAILED,
                    category=AuditLogCategory.DOCUMENT,
                    description=f"PDF generation failed for {agreement.agreement_type}: {exc}",
                    resource=agreement,
                    request=request,
                )
                return Response(
                    {'error': 'Failed to generate the signed document. Please try again.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            agreement.save()
            signed.append(agreement)

            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.AGREEMENT_SIGNED,
                category=AuditLogCategory.DOCUMENT,
                description=f"Signed {agreement.agreement_type} v{agreement.version}",
                resource=agreement,
                data={
                    'contract_id': agreement.contract_id,
                    'otp_reference': agreement.otp_reference,
                },
                request=request,
            )

        return Response(AgreementSerializer(signed, many=True, context={'request': request}).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def agreement_status(request):
    """GET /agreements/status — current user's latest agreement per type."""
    agreements = (
        Agreement.objects
        .filter(user=request.user, status__in=[AgreementStatus.SIGNED, AgreementStatus.PENDING])
        .order_by('agreement_type', '-created_at')
    )
    latest_by_type = {}
    for agreement in agreements:
        latest_by_type.setdefault(agreement.agreement_type, agreement)

    return Response(
        AgreementSerializer(list(latest_by_type.values()), many=True, context={'request': request}).data
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def agreement_download(request, agreement_id):
    """GET /agreements/download/{agreement_id} — signed PDF (B2B/DPA/B2C only)."""
    try:
        agreement = get_by_identifier(Agreement.objects.filter(user=request.user), agreement_id)
    except Agreement.DoesNotExist:
        return Response({'error': 'Agreement not found'}, status=status.HTTP_404_NOT_FOUND)

    if agreement.agreement_type not in DOWNLOADABLE_TYPES:
        return Response({'error': 'This agreement type has no downloadable document.'}, status=status.HTTP_400_BAD_REQUEST)
    if agreement.status != AgreementStatus.SIGNED or not agreement.signed_pdf:
        return Response({'error': 'This agreement has not been signed yet.'}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'url': request.build_absolute_uri(agreement.signed_pdf.url)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def agreement_versions(request):
    """GET /agreements/versions — current version string per agreement type,
    so the frontend never has to hardcode them."""
    return Response(CURRENT_VERSIONS)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def agreement_preview(request, agreement_type):
    """GET /agreements/preview/{agreement_type} — unsigned document body for
    inline review before the user commits to signing."""
    if agreement_type not in dict(AgreementType.CHOICES):
        return Response({'error': 'Unknown agreement type.'}, status=status.HTTP_404_NOT_FOUND)
    if agreement_type not in {AgreementType.B2B_AGREEMENT, AgreementType.DPA, AgreementType.B2C_AGREEMENT}:
        return Response({'error': 'No preview available for this agreement type.'}, status=status.HTTP_400_BAD_REQUEST)

    company = _user_company(request.user)
    version = CURRENT_VERSIONS.get(agreement_type, '')
    html = render_preview_html(agreement_type, version, company=company, user=request.user)
    return Response({'html': html, 'version': version})


@api_view(['GET'])
@permission_classes([AllowAny])
def agreement_verify(request, contract_id):
    """GET /agreements/verify/{contract_id} — public QR verification.
    No auth, no evaluation data, no contact info — matches the fields on
    the printed PDF seal so a holder can cross-check a paper/PDF copy.
    """
    agreement = Agreement.objects.filter(
        contract_id=contract_id,
        status__in=[AgreementStatus.SIGNED, AgreementStatus.SUPERSEDED],
    ).first()
    if not agreement:
        return Response({'error': 'No signed agreement found for this ID.'}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        'contract_id': agreement.contract_id,
        'agreement_type': agreement.agreement_type,
        'agreement_type_display': agreement.get_agreement_type_display(),
        'version': agreement.version,
        'signatory_name': agreement.signatory_name,
        'signed_at': agreement.accepted_at,
        'status': 'SUPERSEDED' if agreement.status == AgreementStatus.SUPERSEDED else 'VALID',
        'pdf_hash': agreement.pdf_hash,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def agreement_audit(request, agreement_id):
    """GET /agreements/audit/{agreement_id} — full audit trail for one
    agreement. Auth: the owning user, or an admin/superadmin."""
    try:
        agreement = get_by_identifier(Agreement.objects.all(), agreement_id)
    except Agreement.DoesNotExist:
        return Response({'error': 'Agreement not found'}, status=status.HTTP_404_NOT_FOUND)

    is_owner = agreement.user_id == request.user.id
    is_admin = request.user.role in {Roles.ADMIN, Roles.SUPERADMIN}
    if not (is_owner or is_admin):
        return Response({'error': 'Not authorized to view this audit trail.'}, status=status.HTTP_403_FORBIDDEN)

    from django.contrib.contenttypes.models import ContentType
    logs = AuditLog.objects.filter(
        resource_type=ContentType.objects.get_for_model(Agreement),
        resource_id=agreement.pk,
    ).order_by('-created_at')
    return Response(AuditLogSerializer(logs, many=True, context={'request': request}).data)
