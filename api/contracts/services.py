import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.core.mail import EmailMessage
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from api.accounts.utils import safe_send_mail
from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity, Roles
from api.core.constants import IdentityVerificationStatus
from api.storage.services import MediaStorageService

from .models import (
    Agreement,
    AgreementEvent,
    AgreementEventType,
    AgreementMethod,
    AgreementStatus,
    AgreementType,
    CookieConsent,
)


@dataclass(frozen=True)
class AgreementDefinition:
    type: str
    label: str
    version: str
    method: str
    user_roles: tuple[str, ...]
    candidate_only: bool = False


AGREEMENT_DEFINITIONS = {
    AgreementType.PRIVACY_TERMS: AgreementDefinition(
        type=AgreementType.PRIVACY_TERMS,
        label="Privacy Policy & Terms of Use",
        version="v1.1",
        method=AgreementMethod.CHECKBOX,
        user_roles=(Roles.B2B, Roles.B2C, Roles.B2B_TEAM_MEMBER),
    ),
    AgreementType.AI_DISCLOSURE: AgreementDefinition(
        type=AgreementType.AI_DISCLOSURE,
        label="AI Transparency & Disclosure",
        version="v1.0",
        method=AgreementMethod.CHECKBOX,
        user_roles=(Roles.B2B, Roles.B2C, Roles.B2B_TEAM_MEMBER),
    ),
    AgreementType.B2B_AGREEMENT: AgreementDefinition(
        type=AgreementType.B2B_AGREEMENT,
        label="B2B Agreement",
        version="Legal",
        method=AgreementMethod.OTP_SIGNATURE,
        user_roles=(Roles.B2B,),
    ),
    AgreementType.DPA: AgreementDefinition(
        type=AgreementType.DPA,
        label="Data Processing Agreement",
        version="v1.2",
        method=AgreementMethod.OTP_SIGNATURE,
        user_roles=(Roles.B2B,),
    ),
    AgreementType.B2C_AGREEMENT: AgreementDefinition(
        type=AgreementType.B2C_AGREEMENT,
        label="B2C Agreement",
        version="v1.1",
        method=AgreementMethod.OTP_SIGNATURE,
        user_roles=(Roles.B2C,),
    ),
    AgreementType.CANDIDATE_CONSENT: AgreementDefinition(
        type=AgreementType.CANDIDATE_CONSENT,
        label="Candidate Consent",
        version="v1.1",
        method=AgreementMethod.OTP_SIGNATURE,
        user_roles=(),
        candidate_only=True,
    ),
}

COOKIE_CONSENT_EXPIRY_DAYS = 365
OTP_TTL_MINUTES = 10
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_ATTEMPTS = 5
VERIFY_BASE_URL = "https://verify.meritleense.com/agreement"


def request_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def request_user_agent(request):
    return request.META.get("HTTP_USER_AGENT", "")


class AgreementService:
    @classmethod
    def get_definition(cls, agreement_type):
        try:
            return AGREEMENT_DEFINITIONS[agreement_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported agreement type: {agreement_type}") from exc

    @classmethod
    def required_agreement_types_for_user(cls, user):
        base = [AgreementType.PRIVACY_TERMS, AgreementType.AI_DISCLOSURE]
        if user.role == Roles.B2B:
            return base + [AgreementType.B2B_AGREEMENT, AgreementType.DPA]
        if user.role == Roles.B2C:
            return base + [AgreementType.B2C_AGREEMENT]
        if user.role == Roles.B2B_TEAM_MEMBER:
            return list(base)
        return []

    @classmethod
    def _build_verify_url(cls, agreement):
        return f"{VERIFY_BASE_URL}/{agreement.public_id}"

    @classmethod
    def _log_event(cls, agreement, event_type, description="", request=None, data=None):
        return AgreementEvent.objects.create(
            agreement=agreement,
            event_type=event_type,
            description=description,
            ip_address=request_ip(request) if request else None,
            user_agent=request_user_agent(request) if request else "",
            data=data or {},
        )

    @classmethod
    def _latest_signed_record(cls, *, user=None, candidate=None, session=None, agreement_type=None):
        queryset = Agreement.objects.filter(
            agreement_type=agreement_type,
            status__in=[AgreementStatus.SIGNED, AgreementStatus.SUPERSEDED],
        )
        if user is not None:
            queryset = queryset.filter(user=user)
        if candidate is not None:
            queryset = queryset.filter(candidate=candidate)
        if session is not None:
            queryset = queryset.filter(session=session)
        return queryset.order_by("-signed_at", "-created_at").first()

    @classmethod
    @transaction.atomic
    def accept_registration_checkboxes(cls, *, user, request, accepted):
        required = {
            AgreementType.PRIVACY_TERMS: bool(accepted.get(AgreementType.PRIVACY_TERMS)),
            AgreementType.AI_DISCLOSURE: bool(accepted.get(AgreementType.AI_DISCLOSURE)),
        }
        if not all(required.values()):
            raise ValueError("Both Privacy Policy & Terms and AI Disclosure must be accepted")

        created = []
        for agreement_type, is_accepted in required.items():
            if not is_accepted:
                continue
            definition = cls.get_definition(agreement_type)
            current = cls._latest_signed_record(user=user, agreement_type=agreement_type)
            if current and current.version == definition.version:
                created.append(current)
                continue

            agreement = Agreement.objects.create(
                user=user,
                subscription=user.subscriptions.order_by("-created_at").first(),
                agreement_type=agreement_type,
                version=definition.version,
                status=AgreementStatus.SIGNED,
                method=definition.method,
                signatory_name=user.get_full_name(),
                signed_at=timezone.now(),
                ip_address=request_ip(request),
                user_agent=request_user_agent(request),
                verification_url=cls._build_verify_url_placeholder(),
            )
            if current and current.status == AgreementStatus.SIGNED:
                current.status = AgreementStatus.SUPERSEDED
                current.save(update_fields=["status", "updated_at"])
                agreement.previous_version = current
                agreement.save(update_fields=["previous_version", "updated_at"])

            cls._log_event(
                agreement,
                AgreementEventType.CHECKBOX_ACCEPTED,
                description=f"{definition.label} accepted",
                request=request,
                data={"version": definition.version, "method": definition.method},
            )
            created.append(agreement)
        return created

    @classmethod
    def _build_verify_url_placeholder(cls):
        return VERIFY_BASE_URL

    @classmethod
    def _build_subject_scope(cls, *, user=None, session=None):
        if user is not None:
            return {"user": user, "candidate": None, "session": None}
        if session is not None:
            return {"user": None, "candidate": session.candidate, "session": session}
        raise ValueError("Either user or session is required")

    @classmethod
    @transaction.atomic
    def initiate_otp_signing(
        cls,
        *,
        agreement_type,
        signatory_name,
        request,
        user=None,
        session=None,
        auth_checkbox_confirmed=False,
        company_stamp=None,
    ):
        definition = cls.get_definition(agreement_type)
        if definition.method != AgreementMethod.OTP_SIGNATURE:
            raise ValueError("This agreement does not use OTP signing")
        if user is not None and user.role not in definition.user_roles:
            raise ValueError("This agreement is not available for your user role")
        if definition.candidate_only and session is None:
            raise ValueError("Candidate consent requires an interview session")
        if agreement_type == AgreementType.B2B_AGREEMENT and not auth_checkbox_confirmed:
            raise ValueError("Authorization checkbox must be confirmed before OTP dispatch")

        scope = cls._build_subject_scope(user=user, session=session)
        existing = cls._latest_signed_record(
            user=scope["user"],
            candidate=scope["candidate"],
            session=scope["session"],
            agreement_type=agreement_type,
        )
        if existing and existing.version == definition.version and existing.status == AgreementStatus.SIGNED:
            return existing, False

        agreement = Agreement.objects.create(
            user=scope["user"],
            candidate=scope["candidate"],
            session=scope["session"],
            subscription=user.subscriptions.order_by("-created_at").first() if user else None,
            agreement_type=agreement_type,
            version=definition.version,
            status=AgreementStatus.PENDING_SIGNATURE,
            method=definition.method,
            signatory_name=signatory_name.strip(),
            auth_checkbox_confirmed=auth_checkbox_confirmed,
            company_stamp=company_stamp,
            ip_address=request_ip(request),
            user_agent=request_user_agent(request),
        )
        if existing and existing.status == AgreementStatus.SIGNED:
            agreement.previous_version = existing
            agreement.save(update_fields=["previous_version", "updated_at"])

        cls._log_event(
            agreement,
            AgreementEventType.CREATED,
            description=f"{definition.label} signing initiated",
            request=request,
            data={"version": definition.version},
        )
        cls._send_otp(agreement, request=request, is_resend=False)
        return agreement, True

    @classmethod
    def _send_otp(cls, agreement, *, request, is_resend):
        now = timezone.now()
        if is_resend and agreement.otp_last_sent_at:
            elapsed = (now - agreement.otp_last_sent_at).total_seconds()
            if elapsed < OTP_RESEND_COOLDOWN_SECONDS:
                remaining = int(OTP_RESEND_COOLDOWN_SECONDS - elapsed)
                raise ValueError(f"OTP resend available in {remaining} seconds")
        if agreement.otp_attempts >= OTP_MAX_ATTEMPTS:
            agreement.status = AgreementStatus.PENDING_REVIEW
            agreement.save(update_fields=["status", "updated_at"])
            raise ValueError("Maximum OTP attempts reached. Please restart signing.")

        agreement.otp_code = f"{secrets.randbelow(900000) + 100000}"
        agreement.otp_reference = secrets.token_hex(8)
        agreement.otp_attempts += 1
        agreement.otp_expires_at = now + timedelta(minutes=OTP_TTL_MINUTES)
        agreement.otp_last_sent_at = now
        agreement.save(
            update_fields=[
                "otp_code",
                "otp_reference",
                "otp_attempts",
                "otp_expires_at",
                "otp_last_sent_at",
                "updated_at",
            ]
        )
        cls._deliver_otp(agreement)
        cls._log_event(
            agreement,
            AgreementEventType.OTP_RESENT if is_resend else AgreementEventType.OTP_SENT,
            description="OTP dispatched for agreement signing",
            request=request,
            data={
                "otp_reference": agreement.otp_reference,
                "expires_at": agreement.otp_expires_at.isoformat() if agreement.otp_expires_at else None,
                "resend_attempts": agreement.otp_attempts,
            },
        )

    @classmethod
    def resend_otp(cls, *, agreement, request):
        if agreement.status != AgreementStatus.PENDING_SIGNATURE:
            raise ValueError("Agreement is not awaiting signature")
        cls._send_otp(agreement, request=request, is_resend=True)
        return agreement

    @classmethod
    def confirm_otp(cls, *, agreement, otp_code, request):
        if agreement.status != AgreementStatus.PENDING_SIGNATURE:
            raise ValueError("Agreement is not awaiting OTP confirmation")
        if not agreement.otp_expires_at or timezone.now() > agreement.otp_expires_at:
            agreement.status = AgreementStatus.PENDING_REVIEW
            agreement.save(update_fields=["status", "updated_at"])
            raise ValueError("OTP has expired. Please restart signing.")
        if agreement.otp_code != str(otp_code).strip():
            agreement.otp_failed_attempts += 1
            update_fields = ["otp_failed_attempts", "updated_at"]
            if agreement.otp_failed_attempts >= OTP_MAX_ATTEMPTS:
                agreement.status = AgreementStatus.PENDING_REVIEW
                update_fields.append("status")
            agreement.save(update_fields=update_fields)
            raise ValueError("Invalid OTP code")

        agreement.status = AgreementStatus.SIGNED
        agreement.signed_at = timezone.now()
        agreement.ip_address = request_ip(request)
        agreement.user_agent = request_user_agent(request)
        agreement.verification_url = cls._build_verify_url(agreement)
        pdf_result = cls.generate_signed_pdf(agreement)
        agreement.pdf_path = pdf_result["storage_key"]
        agreement.pdf_hash = pdf_result["sha256"]
        agreement.verification_url = cls._build_verify_url(agreement)
        if agreement.previous_version_id and agreement.previous_version.status == AgreementStatus.SIGNED:
            agreement.previous_version.status = AgreementStatus.SUPERSEDED
            agreement.previous_version.save(update_fields=["status", "updated_at"])
        agreement.save(
            update_fields=[
                "status",
                "signed_at",
                "ip_address",
                "user_agent",
                "otp_failed_attempts",
                "pdf_path",
                "pdf_hash",
                "verification_url",
                "updated_at",
            ]
        )

        if agreement.session_id:
            agreement.session.candidate_consent_agreement = agreement
            agreement.session.save(update_fields=["candidate_consent_agreement", "updated_at"])

        cls._log_event(
            agreement,
            AgreementEventType.OTP_CONFIRMED,
            description="OTP validated successfully",
            request=request,
            data={"otp_reference": agreement.otp_reference},
        )
        cls._log_event(
            agreement,
            AgreementEventType.SIGNED,
            description="Agreement signed and locked PDF generated",
            request=request,
            data={"pdf_path": agreement.pdf_path, "pdf_hash": agreement.pdf_hash},
        )
        cls._write_audit_log(agreement, request=request, action=AuditLogAction.DOCUMENT_UPLOADED)
        cls._email_signed_agreement(agreement, pdf_result["storage_url"], final_bytes=pdf_result["content"])
        return agreement

    @classmethod
    def generate_signed_pdf(cls, agreement):
        first_pass = cls._render_pdf_bytes(agreement, pdf_hash="PENDING")
        provisional_hash = hashlib.sha256(first_pass).hexdigest()
        final_bytes = cls._render_pdf_bytes(agreement, pdf_hash=provisional_hash)
        final_hash = hashlib.sha256(final_bytes).hexdigest()
        target_path = cls._pdf_target_path(agreement)
        file_details = MediaStorageService.save_bytes(content=final_bytes, target_path=target_path)
        return {
            "storage_key": file_details["storage_key"],
            "storage_url": file_details["storage_url"],
            "sha256": final_hash,
            "content": final_bytes,
        }

    @classmethod
    def _pdf_target_path(cls, agreement):
        owner_segment = str(agreement.user.public_id) if agreement.user_id else str(agreement.session.public_id)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        return f"documents/agreements/{owner_segment}/{agreement.agreement_type}_{agreement.version}_{timestamp}.pdf"

    @classmethod
    def _render_pdf_bytes(cls, agreement, pdf_hash):
        definition = cls.get_definition(agreement.agreement_type)
        signed_at = agreement.signed_at or timezone.now()
        verification_url = cls._build_verify_url(agreement)
        lines = [
            f"MeritLense {definition.label}",
            f"Agreement ID: {agreement.agreement_id}",
            f"Version: {agreement.version}",
            f"Signatory: {agreement.signatory_name}",
            f"Signed At (UTC): {signed_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "Signature Method: OTP-based electronic signature",
            f"OTP Reference: {agreement.otp_reference}",
            f"IP Address: {agreement.ip_address or ''}",
            f"Verification URL: {verification_url}",
            "This document was signed in-platform for MeritLense and locked after OTP confirmation.",
            f"SHA-256: {pdf_hash}",
        ]
        return cls._build_simple_pdf(lines)

    @classmethod
    def _build_simple_pdf(cls, lines):
        def escape_pdf_text(value):
            return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

        content_lines = ["BT", "/F1 12 Tf", "50 780 Td", "14 TL"]
        for index, line in enumerate(lines):
            prefix = "" if index == 0 else "T* "
            content_lines.append(f"{prefix}({escape_pdf_text(line)}) Tj")
        content_lines.append("ET")
        content = "\n".join(content_lines).encode("latin-1", errors="replace")

        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(content)} >>".encode("ascii") + b"\nstream\n" + content + b"\nendstream",
        ]

        pdf = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(pdf))
            pdf.extend(f"{index} 0 obj\n".encode("ascii"))
            pdf.extend(obj)
            pdf.extend(b"\nendobj\n")
        xref_offset = len(pdf)
        pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        pdf.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        pdf.extend(
            (
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{xref_offset}\n%%EOF"
            ).encode("ascii")
        )
        return bytes(pdf)

    @classmethod
    def _write_audit_log(cls, agreement, *, request, action):
        actor = agreement.user if agreement.user_id else agreement.session.created_by
        AuditLogService.log(
            user=actor,
            action=action,
            category=AuditLogCategory.DOCUMENT,
            description=f"{agreement.agreement_type} processed for agreement {agreement.agreement_id}",
            resource=agreement.user if agreement.user_id else agreement.session,
            data={
                "agreement_id": agreement.agreement_id,
                "agreement_type": agreement.agreement_type,
                "version": agreement.version,
                "status": agreement.status,
                "otp_reference": agreement.otp_reference,
                "pdf_hash": agreement.pdf_hash,
            },
            request=request,
            severity=AuditLogSeverity.INFO,
        )

    @classmethod
    def _deliver_otp(cls, agreement):
        recipient = cls._resolve_recipient_email(agreement)
        if not recipient:
            return
        subject = f"Your MeritLense OTP for {cls.get_definition(agreement.agreement_type).label}"
        message = (
            f"Your OTP is {agreement.otp_code}. "
            f"It is valid for {OTP_TTL_MINUTES} minutes. "
            f"Reference: {agreement.otp_reference}."
        )
        safe_send_mail(subject, message, [recipient])

    @classmethod
    def _resolve_recipient_email(cls, agreement):
        if agreement.user_id:
            return agreement.user.email
        if agreement.candidate_id:
            return agreement.candidate.email
        return ""

    @classmethod
    def _email_signed_agreement(cls, agreement, storage_url, final_bytes=None):
        recipient = cls._resolve_recipient_email(agreement)
        if not recipient:
            return 0
        subject = f"Signed MeritLense Agreement: {cls.get_definition(agreement.agreement_type).label}"
        body = (
            f"Agreement ID: {agreement.agreement_id}\n"
            f"Version: {agreement.version}\n"
            f"Signed At (UTC): {agreement.signed_at.strftime('%Y-%m-%d %H:%M:%S') if agreement.signed_at else ''}\n"
            f"Verification URL: {agreement.verification_url}\n"
            f"SHA-256: {agreement.pdf_hash}\n"
        )
        if final_bytes is None and agreement.pdf_path:
            with default_storage.open(agreement.pdf_path, "rb") as handle:
                final_bytes = handle.read()
        email = EmailMessage(subject, body, to=[recipient])
        if final_bytes:
            email.attach(f"{agreement.agreement_id}.pdf", final_bytes, "application/pdf")
        try:
            return email.send(fail_silently=True)
        except Exception:
            return 0

    @classmethod
    def get_status_payload_for_user(cls, user):
        payload = []
        for agreement_type in cls.required_agreement_types_for_user(user):
            definition = cls.get_definition(agreement_type)
            latest = cls._latest_signed_record(user=user, agreement_type=agreement_type)
            payload.append(
                {
                    "agreement_type": agreement_type,
                    "label": definition.label,
                    "version": definition.version,
                    "method": definition.method,
                    "status": latest.status if latest else "missing",
                    "signed_at": latest.signed_at if latest else None,
                    "agreement_id": latest.agreement_id if latest else None,
                    "agreement_public_id": str(latest.public_id) if latest else None,
                    "download_available": bool(latest and latest.pdf_path),
                }
            )
        return payload

    @classmethod
    def version_mismatches_for_user(cls, user):
        mismatches = []
        for agreement_type in cls.required_agreement_types_for_user(user):
            definition = cls.get_definition(agreement_type)
            latest = cls._latest_signed_record(user=user, agreement_type=agreement_type)
            if latest is None or latest.version != definition.version or latest.status != AgreementStatus.SIGNED:
                mismatches.append(
                    {
                        "agreement_type": agreement_type,
                        "label": definition.label,
                        "required_version": definition.version,
                        "current_status": latest.status if latest else "missing",
                        "signed_version": latest.version if latest else None,
                    }
                )
                if latest:
                    cls._log_event(
                        latest,
                        AgreementEventType.VERSION_MISMATCH,
                        description=f"Version mismatch detected for {definition.label}",
                        data={"required_version": definition.version, "signed_version": latest.version},
                    )
        return mismatches

    @classmethod
    def record_verbal_confirmation(cls, *, session, uploaded_file, request):
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        path = f"candidates/{session.public_id}/verbal_confirmation_{timestamp}.webm"
        file_details = MediaStorageService.save_uploaded_file(uploaded_file=uploaded_file, target_path=path)
        session.verbal_confirmation_path = file_details["storage_key"]
        session.verbal_confirmation_recorded_at = timezone.now()
        session.save(
            update_fields=[
                "verbal_confirmation_path",
                "verbal_confirmation_recorded_at",
                "updated_at",
            ]
        )
        agreement = session.candidate_consent_agreement
        if agreement:
            agreement.verbal_audio_path = file_details["storage_key"]
            agreement.save(update_fields=["verbal_audio_path", "updated_at"])
            cls._log_event(
                agreement,
                AgreementEventType.VERBAL_CONFIRMATION_RECORDED,
                description="Candidate verbal confirmation stored",
                request=request,
                data={"audio_path": file_details["storage_key"]},
            )
        return file_details

    @classmethod
    def acknowledge_privacy_notice(cls, *, session, request):
        now = timezone.now()
        session.privacy_notice_acknowledged_at = now
        session.privacy_notice_ip_address = request_ip(request)
        session.save(
            update_fields=[
                "privacy_notice_acknowledged_at",
                "privacy_notice_ip_address",
                "updated_at",
            ]
        )
        if session.candidate_consent_agreement_id:
            cls._log_event(
                session.candidate_consent_agreement,
                AgreementEventType.PRIVACY_NOTICE_ACKNOWLEDGED,
                description="Candidate acknowledged privacy notice before evaluation",
                request=request,
                data={
                    "session_id": str(session.public_id),
                    "acknowledged_at": now.isoformat(),
                },
            )
        return session

    @classmethod
    def record_device_check(cls, *, session):
        session.device_check_completed_at = timezone.now()
        session.save(update_fields=["device_check_completed_at", "updated_at"])
        return session

    @classmethod
    def record_identity_verification(cls, *, session, face_match_score, single_face_detected, request=None):
        score = float(face_match_score)
        session.face_match_score = score
        session.single_face_detected = single_face_detected
        if single_face_detected and score >= 85.0:
            session.identity_verified = True
            session.verification_status = IdentityVerificationStatus.VERIFIED
        else:
            session.identity_verified = False
            session.verification_status = IdentityVerificationStatus.FAILED
        session.save(
            update_fields=[
                "face_match_score",
                "single_face_detected",
                "identity_verified",
                "verification_status",
                "updated_at",
            ]
        )
        return session


class CookieConsentService:
    @classmethod
    def record_consent(cls, *, user, visitor_key, categories_accepted, request):
        expires_at = timezone.now() + timedelta(days=COOKIE_CONSENT_EXPIRY_DAYS)
        return CookieConsent.objects.create(
            user=user,
            visitor_key=visitor_key or "",
            ip_address=request_ip(request),
            user_agent=request_user_agent(request),
            categories_accepted=categories_accepted,
            expires_at=expires_at,
        )

    @classmethod
    def get_current_for_user(cls, user):
        return CookieConsent.objects.filter(user=user).order_by("-created_at").first()

    @classmethod
    def get_current_for_visitor(cls, visitor_key):
        return CookieConsent.objects.filter(visitor_key=visitor_key).order_by("-created_at").first()


def open_signed_pdf(agreement):
    if not agreement.pdf_path:
        raise FileNotFoundError("No signed PDF is available for this agreement")
    return default_storage.open(agreement.pdf_path, "rb")
