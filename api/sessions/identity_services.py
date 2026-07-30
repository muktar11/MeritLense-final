from decimal import Decimal, InvalidOperation
from pathlib import Path
import shutil
import subprocess
import tempfile

import requests
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from api.audit.services import AuditLogService
from api.contracts.constants import CURRENT_VERSIONS
from api.contracts.models import Agreement
from api.core.constants import (
    AgreementMethod,
    AgreementStatus,
    AgreementType,
    AuditLogAction,
    AuditLogCategory,
    AuditLogSeverity,
    IdentityVerificationStatus,
    InterviewSessionStatus,
)

from .models import IntegrityLog, SessionArtifact


class IdentityVerificationError(Exception):
    def __init__(self, message, *, code="identity_verification_error", metadata=None):
        super().__init__(message)
        self.code = code
        self.metadata = metadata or {}


class IdentityVerificationConfigurationError(IdentityVerificationError):
    pass


def _parse_decimal(value):
    if value in (None, ""):
        return None
    try:
        score = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if score < Decimal("0"):
        score = Decimal("0")
    if score > Decimal("100"):
        score = Decimal("100")
    return score.quantize(Decimal("0.01"))


def _coerce_bool(value, *, default=False):
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "pass", "passed", "verified"}:
        return True
    if normalized in {"false", "0", "no", "n", "fail", "failed"}:
        return False
    return default


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


class DynamicIdentityVerificationProvider:
    def __init__(self):
        self.provider = getattr(settings, "IDENTITY_VERIFICATION_PROVIDER", "DIRECT")
        self.api_url = getattr(settings, "IDENTITY_VERIFICATION_API_URL", "")
        self.frame_api_url = getattr(settings, "IDENTITY_VERIFICATION_FRAME_API_URL", self.api_url)
        self.api_key = getattr(settings, "IDENTITY_VERIFICATION_API_KEY", "")
        self.timeout_seconds = getattr(settings, "IDENTITY_VERIFICATION_TIMEOUT_SECONDS", 20)
        self.match_threshold = Decimal(str(getattr(settings, "IDENTITY_VERIFICATION_MATCH_THRESHOLD", "85.0")))

    @property
    def is_configured(self):
        return bool(self.api_url and self.api_key)

    @property
    def frame_analysis_is_configured(self):
        return bool(self.frame_api_url and self.api_key)

    def verify(
        self,
        *,
        session,
        provider_result=None,
        face_match_score=None,
        single_face_detected=None,
        liveness_passed=None,
        metadata=None,
        id_document_file=None,
        selfie_file=None,
    ):
        payload = provider_result or {}
        if payload:
            return self._normalize_result(payload, metadata=metadata)

        if self.is_configured and (id_document_file is not None or selfie_file is not None):
            return self._call_remote_provider(
                session=session,
                metadata=metadata,
                id_document_file=id_document_file,
                selfie_file=selfie_file,
            )

        if face_match_score is None and single_face_detected is None and liveness_passed is None:
            raise IdentityVerificationConfigurationError(
                "Identity verification requires either direct provider results, explicit verification values, or a configured provider endpoint.",
                code="identity_verification_not_configured",
            )

        return self._normalize_result(
            {
                "face_match_score": face_match_score,
                "single_face_detected": single_face_detected,
                "liveness_passed": liveness_passed,
            },
            metadata=metadata,
        )

    def _call_remote_provider(self, *, session, metadata=None, id_document_file=None, selfie_file=None):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "session_id": str(session.public_id),
            "candidate_id": str(session.candidate.public_id),
        }
        if metadata:
            for key, value in metadata.items():
                data[f"metadata_{key}"] = value

        files = {}
        if id_document_file is not None:
            files["id_document"] = (
                id_document_file.name,
                id_document_file,
                getattr(id_document_file, "content_type", "application/octet-stream"),
            )
        if selfie_file is not None:
            files["selfie_image"] = (
                selfie_file.name,
                selfie_file,
                getattr(selfie_file, "content_type", "application/octet-stream"),
            )

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                data=data,
                files=files,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise IdentityVerificationError(
                "Identity verification provider timed out.",
                code="identity_verification_timeout",
            ) from exc
        except requests.RequestException as exc:
            raise IdentityVerificationError(
                "Identity verification provider request failed.",
                code="identity_verification_request_failed",
                metadata={"status_code": getattr(exc.response, "status_code", None)},
            ) from exc

        return self._normalize_result(response.json(), metadata=metadata)

    def analyze_frame(
        self,
        *,
        session,
        frame_file=None,
        provider_result=None,
        single_face_detected=None,
        face_count=None,
        liveness_passed=None,
        metadata=None,
    ):
        payload = provider_result or {}
        if payload:
            return self._normalize_frame_result(payload, metadata=metadata)

        if self.frame_analysis_is_configured and frame_file is not None:
            return self._call_remote_frame_provider(
                session=session,
                frame_file=frame_file,
                metadata=metadata,
            )

        if single_face_detected is None and face_count is None and liveness_passed is None:
            raise IdentityVerificationConfigurationError(
                "Integrity frame analysis requires either direct provider results, explicit frame analysis values, or a configured provider endpoint.",
                code="integrity_analysis_not_configured",
            )

        return self._normalize_frame_result(
            {
                "single_face_detected": single_face_detected,
                "face_count": face_count,
                "liveness_passed": liveness_passed,
            },
            metadata=metadata,
        )

    def _call_remote_frame_provider(self, *, session, frame_file, metadata=None):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "session_id": str(session.public_id),
            "candidate_id": str(session.candidate.public_id),
        }
        if metadata:
            for key, value in metadata.items():
                data[f"metadata_{key}"] = value

        files = {
            "frame_image": (
                frame_file.name,
                frame_file,
                getattr(frame_file, "content_type", "application/octet-stream"),
            )
        }

        try:
            response = requests.post(
                self.frame_api_url,
                headers=headers,
                data=data,
                files=files,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise IdentityVerificationError(
                "Integrity analysis provider timed out.",
                code="integrity_analysis_timeout",
            ) from exc
        except requests.RequestException as exc:
            raise IdentityVerificationError(
                "Integrity analysis provider request failed.",
                code="integrity_analysis_request_failed",
                metadata={"status_code": getattr(exc.response, "status_code", None)},
            ) from exc

        return self._normalize_frame_result(response.json(), metadata=metadata)

    def _normalize_result(self, payload, *, metadata=None):
        metadata = metadata or {}
        raw_score = payload.get("face_match_score")
        if raw_score is None:
            raw_score = payload.get("similarity_score")
        if raw_score is None:
            raw_score = payload.get("score")
        face_match_score = _parse_decimal(raw_score)
        face_count = payload.get("face_count")

        if face_count not in (None, ""):
            try:
                face_count = int(face_count)
            except (TypeError, ValueError):
                face_count = None

        single_face_detected = payload.get("single_face_detected")
        if single_face_detected in (None, "") and face_count is not None:
            single_face_detected = face_count == 1
        single_face_detected = _coerce_bool(single_face_detected, default=False)

        liveness_passed = _coerce_bool(payload.get("liveness_passed"), default=True)
        explicit_status = str(payload.get("verification_status") or "").strip().upper()
        reason = str(payload.get("reason") or payload.get("verification_reason") or "").strip()

        if explicit_status in {
            IdentityVerificationStatus.VERIFIED,
            IdentityVerificationStatus.FAILED,
            IdentityVerificationStatus.PENDING,
        }:
            verification_status = explicit_status
        else:
            verified = (
                face_match_score is not None
                and face_match_score >= self.match_threshold
                and single_face_detected
                and liveness_passed
            )
            verification_status = (
                IdentityVerificationStatus.VERIFIED if verified else IdentityVerificationStatus.FAILED
            )

        if not reason:
            if verification_status == IdentityVerificationStatus.VERIFIED:
                reason = "Identity verification passed."
            elif face_match_score is not None and face_match_score < self.match_threshold:
                reason = f"Face match score below threshold ({face_match_score} < {self.match_threshold})."
            elif not single_face_detected:
                reason = "A single candidate face was not detected."
            elif not liveness_passed:
                reason = "Liveness verification did not pass."
            else:
                reason = "Identity verification failed."

        provider_name = str(payload.get("provider") or self.provider or "DIRECT").upper()

        return {
            "provider": provider_name,
            "verification_status": verification_status,
            "identity_verified": verification_status == IdentityVerificationStatus.VERIFIED,
            "face_match_score": face_match_score,
            "single_face_detected": single_face_detected,
            "liveness_passed": liveness_passed,
            "reason": reason,
            "metadata": {
                **metadata,
                "provider_payload": _json_safe(payload),
                "face_count": face_count,
            },
        }

    def _normalize_frame_result(self, payload, *, metadata=None):
        metadata = metadata or {}
        raw_face_count = payload.get("face_count")
        if raw_face_count in (None, ""):
            raw_face_count = payload.get("faces_detected")
        try:
            face_count = int(raw_face_count) if raw_face_count not in (None, "") else None
        except (TypeError, ValueError):
            face_count = None

        single_face_detected = payload.get("single_face_detected")
        if single_face_detected in (None, "") and face_count is not None:
            single_face_detected = face_count == 1
        single_face_detected = _coerce_bool(single_face_detected, default=False)

        liveness_passed = _coerce_bool(payload.get("liveness_passed"), default=True)
        no_face_detected = face_count == 0 if face_count is not None else not single_face_detected
        multiple_faces_detected = face_count > 1 if face_count is not None else False

        if no_face_detected:
            event_type = "NO_FACE_DETECTED"
            severity = "WARNING"
            reason = "No face was detected in the webcam frame."
        elif multiple_faces_detected:
            event_type = "MULTIPLE_FACES_DETECTED"
            severity = "WARNING"
            reason = f"Multiple faces were detected in the webcam frame ({face_count})."
        elif not liveness_passed:
            event_type = "LIVENESS_CHECK_FAILED"
            severity = "WARNING"
            reason = "Liveness verification failed for the webcam frame."
        else:
            event_type = "SINGLE_FACE_CONFIRMED"
            severity = "INFO"
            reason = "A single live face was confirmed in the webcam frame."

        provider_name = str(payload.get("provider") or self.provider or "DIRECT").upper()
        return {
            "provider": provider_name,
            "event_type": event_type,
            "severity": severity,
            "single_face_detected": single_face_detected,
            "face_count": face_count,
            "liveness_passed": liveness_passed,
            "reason": reason,
            "metadata": {
                **metadata,
                "provider_payload": _json_safe(payload),
            },
        }


class InterviewSessionPrecheckService:
    ARTIFACT_ID_DOCUMENT = "ID_DOCUMENT"
    ARTIFACT_SELFIE_IMAGE = "SELFIE_IMAGE"
    ARTIFACT_WEBCAM_FRAME = "WEBCAM_FRAME"
    ARTIFACT_VERBAL_CONFIRMATION = "VERBAL_CONFIRMATION"

    @classmethod
    def record_candidate_consent(cls, session, *, signatory_name, ip_address=None, user_agent="", actor=None):
        agreement = Agreement.objects.create(
            user=session.created_by,
            company=session.organization,
            agreement_type=AgreementType.CANDIDATE_CONSENT,
            version=CURRENT_VERSIONS[AgreementType.CANDIDATE_CONSENT],
            method=AgreementMethod.CHECKBOX,
            status=AgreementStatus.SIGNED,
            signatory_name=signatory_name.strip(),
            accepted_at=timezone.now(),
            ip_address=ip_address,
            user_agent=user_agent or "",
        )
        session.candidate_consent_agreement = agreement
        session.save(update_fields=["candidate_consent_agreement", "updated_at"])

        cls._log_event(
            session=session,
            actor=actor,
            action=AuditLogAction.AGREEMENT_ACCEPTED,
            description="Candidate consent captured for interview session.",
            data={
                "session_id": str(session.public_id),
                "candidate_id": str(session.candidate.public_id),
                "agreement_id": str(agreement.public_id),
                "agreement_type": AgreementType.CANDIDATE_CONSENT,
            },
        )
        cls._mark_ready_if_complete(session, actor=actor)
        return agreement

    @classmethod
    def record_privacy_acknowledgement(cls, session, *, ip_address=None, actor=None, metadata=None):
        session.privacy_notice_acknowledged_at = timezone.now()
        session.privacy_notice_ip_address = ip_address
        session.save(
            update_fields=[
                "privacy_notice_acknowledged_at",
                "privacy_notice_ip_address",
                "updated_at",
            ]
        )
        cls._log_event(
            session=session,
            actor=actor,
            action=AuditLogAction.AGREEMENT_ACCEPTED,
            description="Candidate acknowledged privacy notice for interview session.",
            data={
                "session_id": str(session.public_id),
                "metadata": metadata or {},
            },
        )
        cls._mark_ready_if_complete(session, actor=actor)
        return session

    @classmethod
    def record_device_check(cls, session, *, passed, actor=None, metadata=None):
        if not passed:
            raise IdentityVerificationError(
                "Device check must pass before the session can continue.",
                code="device_check_failed",
            )
        session.device_check_completed_at = timezone.now()
        session.save(update_fields=["device_check_completed_at", "updated_at"])
        cls._log_event(
            session=session,
            actor=actor,
            action=AuditLogAction.SESSION_READY,
            description="Candidate device check completed.",
            data={
                "session_id": str(session.public_id),
                "metadata": metadata or {},
            },
            severity=AuditLogSeverity.INFO,
        )
        cls._mark_ready_if_complete(session, actor=actor)
        return session

    @classmethod
    def record_verbal_confirmation(cls, session, *, recording_file=None, recording_text="", actor=None, metadata=None):
        artifact = None
        stored_path = recording_text.strip()
        if recording_file is not None:
            artifact = cls._store_uploaded_artifact(
                session=session,
                uploaded_file=recording_file,
                artifact_type=cls.ARTIFACT_VERBAL_CONFIRMATION,
                actor=actor,
                metadata=metadata,
            )
            stored_path = artifact.file.name

        if not stored_path:
            raise IdentityVerificationError(
                "Verbal confirmation requires a recording file or stored recording path.",
                code="verbal_confirmation_missing",
            )

        session.verbal_confirmation_path = stored_path
        session.verbal_confirmation_recorded_at = timezone.now()
        session.save(
            update_fields=[
                "verbal_confirmation_path",
                "verbal_confirmation_recorded_at",
                "updated_at",
            ]
        )
        cls._log_event(
            session=session,
            actor=actor,
            action=AuditLogAction.RESPONSE_ATTACHED,
            description="Candidate verbal confirmation captured for interview session.",
            data={
                "session_id": str(session.public_id),
                "artifact_id": str(artifact.public_id) if artifact else None,
                "metadata": metadata or {},
            },
        )
        cls._mark_ready_if_complete(session, actor=actor)
        return artifact, session

    @classmethod
    def submit_identity_verification(
        cls,
        session,
        *,
        id_document_file=None,
        selfie_file=None,
        provider_result=None,
        face_match_score=None,
        single_face_detected=None,
        liveness_passed=None,
        metadata=None,
        actor=None,
    ):
        verification_metadata, resolved_id_document_file, provider_id_document_file = cls._resolve_identity_reference_inputs(
            session=session,
            id_document_file=id_document_file,
            selfie_file=selfie_file,
            provider_result=provider_result,
            face_match_score=face_match_score,
            single_face_detected=single_face_detected,
            liveness_passed=liveness_passed,
            metadata=metadata,
        )

        verification = DynamicIdentityVerificationProvider().verify(
            session=session,
            provider_result=provider_result,
            face_match_score=face_match_score,
            single_face_detected=single_face_detected,
            liveness_passed=liveness_passed,
            metadata=verification_metadata,
            id_document_file=provider_id_document_file,
            selfie_file=selfie_file,
        )

        artifacts = []
        if id_document_file is not None:
            if hasattr(id_document_file, "seek"):
                id_document_file.seek(0)
            artifacts.append(
                cls._store_uploaded_artifact(
                    session=session,
                    uploaded_file=id_document_file,
                    artifact_type=cls.ARTIFACT_ID_DOCUMENT,
                    actor=actor,
                    metadata=metadata,
                )
            )
        elif resolved_id_document_file is not None:
            verification["metadata"] = {
                **(verification.get("metadata") or {}),
                "stored_reference_document_name": getattr(resolved_id_document_file, "name", ""),
            }
        if selfie_file is not None:
            if hasattr(selfie_file, "seek"):
                selfie_file.seek(0)
            artifacts.append(
                cls._store_uploaded_artifact(
                    session=session,
                    uploaded_file=selfie_file,
                    artifact_type=cls.ARTIFACT_SELFIE_IMAGE,
                    actor=actor,
                    metadata=metadata,
                )
            )

        session.verification_status = verification["verification_status"]
        session.identity_verified = verification["identity_verified"]
        session.face_match_score = verification["face_match_score"]
        session.single_face_detected = verification["single_face_detected"]
        session.save(
            update_fields=[
                "verification_status",
                "identity_verified",
                "face_match_score",
                "single_face_detected",
                "updated_at",
            ]
        )

        severity = AuditLogSeverity.INFO if verification["identity_verified"] else AuditLogSeverity.WARNING
        cls._log_event(
            session=session,
            actor=actor,
            action=AuditLogAction.SESSION_VERIFICATION_PENDING,
            description="Candidate identity verification submitted.",
            severity=severity,
            data={
                "session_id": str(session.public_id),
                "verification_status": verification["verification_status"],
                "face_match_score": str(verification["face_match_score"]) if verification["face_match_score"] is not None else None,
                "single_face_detected": verification["single_face_detected"],
                "liveness_passed": verification["liveness_passed"],
                "reason": verification["reason"],
                "provider": verification["provider"],
                "artifact_ids": [str(artifact.public_id) for artifact in artifacts],
                "metadata": _json_safe(verification["metadata"]),
            },
        )

        if not verification["identity_verified"]:
            from .services import InterviewSessionService

            InterviewSessionService.log_integrity_event(
                session,
                event_type="IDENTITY_VERIFICATION_FAILED",
                severity="WARNING",
                details={
                    "reason": verification["reason"],
                    "provider": verification["provider"],
                    "face_match_score": str(verification["face_match_score"]) if verification["face_match_score"] is not None else None,
                    "single_face_detected": verification["single_face_detected"],
                    "liveness_passed": verification["liveness_passed"],
                    "metadata": _json_safe(verification["metadata"]),
                },
            )
        cls._mark_ready_if_complete(session, actor=actor)
        return verification, artifacts

    @classmethod
    def _resolve_identity_reference_inputs(
        cls,
        *,
        session,
        id_document_file=None,
        selfie_file=None,
        provider_result=None,
        face_match_score=None,
        single_face_detected=None,
        liveness_passed=None,
        metadata=None,
    ):
        metadata = dict(metadata or {})
        candidate_verification_photo = getattr(session.candidate, "verification_photo", None)
        candidate_passport_document = getattr(session.candidate, "passport_document", None)
        resolved_id_document_file = id_document_file or candidate_verification_photo or candidate_passport_document
        provider_id_document_file = resolved_id_document_file

        if id_document_file is not None:
            metadata.setdefault("reference_document_source", "uploaded_id_document")
            metadata.setdefault("reused_candidate_passport", False)
        elif candidate_verification_photo and resolved_id_document_file is candidate_verification_photo:
            # An AI-cropped, user-confirmed face photo derived from the passport
            # at upload time - see Candidate.verification_photo. Not itself a
            # fresh upload for this session, but distinct from the raw
            # passport document for audit purposes.
            metadata.setdefault("reference_document_source", "candidate_verification_photo")
            metadata.setdefault("reused_candidate_passport", False)
        elif resolved_id_document_file:
            metadata.setdefault("reference_document_source", "candidate_passport_document")
            metadata.setdefault("reused_candidate_passport", True)

        should_prepare_provider_reference = (
            provider_result in (None, {})
            and (
                selfie_file is not None
                or resolved_id_document_file is not None
                or face_match_score is None
                or single_face_detected is None
                or liveness_passed is None
            )
        )
        if should_prepare_provider_reference and provider_id_document_file is not None and cls._is_pdf_reference_file(provider_id_document_file):
            try:
                provider_id_document_file = cls._convert_pdf_reference_to_image(provider_id_document_file)
                metadata["provider_reference_source"] = "pdf_first_page_image"
                metadata["provider_reference_fallback_reason"] = "pdf_reference_converted_to_image"
            except IdentityVerificationError as conversion_error:
                profile_photo = getattr(session.candidate, "profile_photo", None)
                if profile_photo:
                    provider_id_document_file = profile_photo
                    metadata["provider_reference_source"] = "candidate_profile_photo"
                    metadata["provider_reference_fallback_reason"] = "pdf_conversion_failed"
                    metadata["pdf_conversion_error"] = str(conversion_error)
                else:
                    raise IdentityVerificationError(
                        "Passport PDF could not be converted into a face-match image and no profile photo is available. Upload an image ID document or add a profile photo.",
                        code="identity_verification_reference_image_required",
                        metadata={"conversion_error": str(conversion_error)},
                    ) from conversion_error
        elif provider_id_document_file is not None:
            metadata.setdefault("provider_reference_source", metadata.get("reference_document_source"))

        for candidate_file in (provider_id_document_file, selfie_file):
            if candidate_file is not None:
                cls._rewind_file(candidate_file)

        return metadata, resolved_id_document_file, provider_id_document_file

    @classmethod
    def resolve_reference_image_file(cls, candidate):
        """Resolve the candidate's own reference image for display/client-side comparison.

        Reuses the same PDF-conversion and profile-photo-fallback logic as the
        provider-facing verification flow above, but only ever looks at the
        candidate's stored documents (no fresh upload involved), since this is
        used to hand a usable reference image back to the candidate's own
        browser for client-side face matching.
        """
        verification_photo = getattr(candidate, "verification_photo", None)
        if verification_photo:
            # An AI-cropped, user-confirmed close-up of the face - produced at
            # upload time specifically to be a reliable verification reference,
            # so prefer it over the raw passport scan whenever it exists. Never
            # needs PDF conversion (it's always saved as a JPEG crop).
            return verification_photo

        passport = getattr(candidate, "passport_document", None)
        profile_photo = getattr(candidate, "profile_photo", None)

        if not passport:
            if profile_photo:
                return profile_photo
            raise IdentityVerificationError(
                "No reference image is available for this candidate. Upload a passport or profile photo.",
                code="identity_verification_reference_image_required",
            )

        if not cls._is_pdf_reference_file(passport):
            return passport

        try:
            return cls._convert_pdf_reference_to_image(passport)
        except IdentityVerificationError as conversion_error:
            if profile_photo:
                return profile_photo
            raise IdentityVerificationError(
                "Passport PDF could not be converted into a face-match image and no profile photo is available. Upload an image ID document or add a profile photo.",
                code="identity_verification_reference_image_required",
                metadata={"conversion_error": str(conversion_error)},
            ) from conversion_error

    @staticmethod
    def _is_pdf_reference_file(uploaded_file):
        content_type = str(getattr(uploaded_file, "content_type", "") or "").lower()
        if content_type == "application/pdf":
            return True
        suffix = Path(getattr(uploaded_file, "name", "") or "").suffix.lower()
        return suffix == ".pdf"

    @staticmethod
    def _rewind_file(uploaded_file):
        if uploaded_file is None:
            return
        if hasattr(uploaded_file, "open"):
            try:
                uploaded_file.open("rb")
            except Exception:
                pass
        if hasattr(uploaded_file, "seek"):
            try:
                uploaded_file.seek(0)
            except Exception:
                pass

    @classmethod
    def _convert_pdf_reference_to_image(cls, uploaded_file):
        cls._rewind_file(uploaded_file)
        pdftoppm_path = shutil.which("pdftoppm")
        if not pdftoppm_path:
            raise IdentityVerificationError(
                "PDF identity reference conversion is not available because pdftoppm is not installed.",
                code="identity_verification_pdf_conversion_unavailable",
            )

        with tempfile.TemporaryDirectory(prefix="identity-pdf-") as tmpdir:
            source_path = Path(tmpdir) / "reference.pdf"
            output_prefix = str(Path(tmpdir) / "reference-page")
            with open(source_path, "wb") as source_file:
                if hasattr(uploaded_file, "chunks"):
                    for chunk in uploaded_file.chunks():
                        source_file.write(chunk)
                else:
                    source_file.write(uploaded_file.read())
            cls._rewind_file(uploaded_file)

            try:
                subprocess.run(
                    [
                        pdftoppm_path,
                        "-f",
                        "1",
                        "-l",
                        "1",
                        "-singlefile",
                        "-png",
                        str(source_path),
                        output_prefix,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                raise IdentityVerificationError(
                    "Failed to convert passport PDF into an image reference.",
                    code="identity_verification_pdf_conversion_failed",
                    metadata={"stderr": exc.stderr[-500:] if exc.stderr else ""},
                ) from exc

            image_path = Path(f"{output_prefix}.png")
            if not image_path.exists():
                raise IdentityVerificationError(
                    "Passport PDF conversion completed without producing an image.",
                    code="identity_verification_pdf_conversion_missing_output",
                )

            image_bytes = image_path.read_bytes()
            image_name = f"{Path(getattr(uploaded_file, 'name', 'passport')).stem}-page-1.png"
            return SimpleUploadedFile(
                image_name,
                image_bytes,
                content_type="image/png",
            )

    @classmethod
    def ingest_integrity_event(
        cls,
        session,
        *,
        event_type="",
        severity="INFO",
        details=None,
        frame_file=None,
        provider_result=None,
        single_face_detected=None,
        face_count=None,
        liveness_passed=None,
        auto_analyze=False,
        actor=None,
    ):
        artifact = None
        details = details or {}
        analysis = None
        if auto_analyze or provider_result or single_face_detected is not None or face_count is not None or liveness_passed is not None:
            analysis = DynamicIdentityVerificationProvider().analyze_frame(
                session=session,
                frame_file=frame_file,
                provider_result=provider_result,
                single_face_detected=single_face_detected,
                face_count=face_count,
                liveness_passed=liveness_passed,
                metadata=details,
            )
            event_type = event_type or analysis["event_type"]
            severity = analysis["severity"]
            details = {
                **details,
                "provider": analysis["provider"],
                "reason": analysis["reason"],
                "single_face_detected": analysis["single_face_detected"],
                "face_count": analysis["face_count"],
                "liveness_passed": analysis["liveness_passed"],
                "analysis_metadata": _json_safe(analysis["metadata"]),
            }

        if not event_type:
            raise IdentityVerificationError(
                "event_type is required when no automatic integrity analysis is provided.",
                code="integrity_event_type_required",
            )
        if frame_file is not None:
            if hasattr(frame_file, "seek"):
                frame_file.seek(0)
            artifact = cls._store_uploaded_artifact(
                session=session,
                uploaded_file=frame_file,
                artifact_type=cls.ARTIFACT_WEBCAM_FRAME,
                actor=actor,
                metadata=details,
            )
            details = {
                **details,
                "artifact_id": str(artifact.public_id),
                "artifact_type": cls.ARTIFACT_WEBCAM_FRAME,
            }

        from .services import InterviewSessionService

        log = InterviewSessionService.log_integrity_event(
            session,
            event_type=event_type,
            severity=severity,
            details=details,
        )

        single_face_detected = details.get("single_face_detected")
        if single_face_detected is not None:
            session.single_face_detected = _coerce_bool(single_face_detected, default=session.single_face_detected)
            session.save(update_fields=["single_face_detected", "updated_at"])
        elif analysis is not None:
            session.single_face_detected = analysis["single_face_detected"]
            session.save(update_fields=["single_face_detected", "updated_at"])

        cls._apply_integrity_escalation(session=session, log=log, actor=actor)

        return log, artifact, analysis

    MULTIPLE_FACES_TERMINATION_THRESHOLD = 3

    # Both are unambiguous integrity problems, distinct from a brief "no
    # face" reading (candidate stepping out of frame momentarily, which
    # stays a soft, non-counted nudge): a second person in frame, or the
    # camera going off entirely (turned off, permission revoked, device
    # disconnected) - the latter means nothing is being observed at all,
    # which is at least as serious as a second person appearing. Both share
    # the same violation counter and threshold.
    ESCALATING_EVENT_TYPES = {"MULTIPLE_FACES_DETECTED", "CAMERA_UNAVAILABLE"}

    @classmethod
    def _apply_integrity_escalation(cls, *, session, log, actor=None):
        from .services import InterviewSessionService, InterviewSessionSocketRegistry, session_event_payload

        if log.event_type in cls.ESCALATING_EVENT_TYPES:
            previous = (
                IntegrityLog.objects.filter(session=session)
                .exclude(pk=log.pk)
                .order_by("-detected_at")
                .first()
            )
            # A new onset is either the first violation ever, or a change
            # from whatever the previous log entry was - including a switch
            # between the two escalating types (e.g. camera comes back on
            # but now shows a second person). A sustained instance of the
            # *same* problem across repeated polls doesn't double-count.
            is_new_onset = previous is None or previous.event_type != log.event_type
            if not is_new_onset:
                return

            session.integrity_violation_count += 1
            session.save(update_fields=["integrity_violation_count", "updated_at"])

            if session.integrity_violation_count >= cls.MULTIPLE_FACES_TERMINATION_THRESHOLD:
                session.terminate_for_integrity()
                cls._log_event(
                    session=session,
                    actor=actor,
                    action=AuditLogAction.SESSION_FAILED,
                    description=f"Interview session terminated for {session.candidate.get_full_name()} after repeated integrity violations",
                    severity=AuditLogSeverity.WARNING,
                    data={"reason": log.event_type, "violation_count": session.integrity_violation_count},
                )
                event_name = "SESSION_FAILED"
            elif session.status == InterviewSessionStatus.IN_PROGRESS:
                session.pause_for_integrity()
                cls._log_event(
                    session=session,
                    actor=actor,
                    action=AuditLogAction.SESSION_PAUSED,
                    description=f"Interview session paused for {session.candidate.get_full_name()} - {log.event_type.replace('_', ' ').lower()}",
                    severity=AuditLogSeverity.WARNING,
                    data={"reason": log.event_type, "violation_count": session.integrity_violation_count},
                )
                event_name = "SESSION_PAUSED"
            else:
                return

            InterviewSessionSocketRegistry.broadcast_sync(
                str(session.public_id),
                {"event": event_name, **session_event_payload(session)},
            )
            InterviewSessionService._broadcast_to_channel_layer(
                str(session.public_id),
                {"event": event_name, **session_event_payload(session)},
            )
        elif log.event_type == "SINGLE_FACE_CONFIRMED" and session.status == InterviewSessionStatus.PAUSED:
            session.resume_from_pause()
            cls._log_event(
                session=session,
                actor=actor,
                action=AuditLogAction.SESSION_RESUMED,
                description=f"Interview session resumed for {session.candidate.get_full_name()}",
                data={"reason": "SINGLE_FACE_CONFIRMED"},
            )
            InterviewSessionSocketRegistry.broadcast_sync(
                str(session.public_id),
                {"event": "SESSION_RESUMED", **session_event_payload(session)},
            )
            InterviewSessionService._broadcast_to_channel_layer(
                str(session.public_id),
                {"event": "SESSION_RESUMED", **session_event_payload(session)},
            )

    @classmethod
    def get_precheck_status_payload(cls, session):
        face_match_score = None
        if session.face_match_score is not None:
            face_match_score = str(session.face_match_score)
        return {
            "session_id": str(session.public_id),
            "status": session.status,
            "candidate_prechecks_complete": session.candidate_prechecks_complete(),
            "verification_status": session.verification_status,
            "identity_verified": session.identity_verified,
            "face_match_score": face_match_score,
            "single_face_detected": session.single_face_detected,
            "candidate_consent_completed": bool(session.candidate_consent_agreement_id),
            "privacy_notice_acknowledged": bool(session.privacy_notice_acknowledged_at),
            "device_check_completed": bool(session.device_check_completed_at),
            "verbal_confirmation_completed": bool(session.verbal_confirmation_recorded_at),
            "latest_integrity_event": cls._latest_integrity_event_payload(session),
        }

    @classmethod
    def _latest_integrity_event_payload(cls, session):
        event = session.integrity_logs.order_by("-detected_at").first()
        if event is None:
            return None
        return {
            "event_type": event.event_type,
            "severity": event.severity,
            "detected_at": event.detected_at,
            "details": event.details,
        }

    @classmethod
    def _store_uploaded_artifact(cls, *, session, uploaded_file, artifact_type, actor=None, metadata=None):
        artifact = SessionArtifact.objects.create(
            session=session,
            candidate=session.candidate,
            artifact_type=artifact_type,
            file=uploaded_file,
            mime_type=getattr(uploaded_file, "content_type", "") or "",
            file_size_bytes=getattr(uploaded_file, "size", 0) or 0,
            metadata=metadata or {},
            uploaded_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
        return artifact

    @classmethod
    def _mark_ready_if_complete(cls, session, actor=None):
        if not session.candidate_prechecks_complete():
            return session
        if session.status not in {
            InterviewSessionStatus.CREATED,
            InterviewSessionStatus.VERIFICATION_PENDING,
        }:
            return session
        session.status = InterviewSessionStatus.READY
        session.save(update_fields=["status", "updated_at"])
        cls._log_event(
            session=session,
            actor=actor,
            action=AuditLogAction.SESSION_READY,
            description="Interview session is ready after candidate prechecks completed.",
            data={"session_id": str(session.public_id)},
        )
        return session

    @classmethod
    def _log_event(
        cls,
        *,
        session,
        actor,
        action,
        description,
        data,
        severity=AuditLogSeverity.INFO,
    ):
        if actor is not None:
            AuditLogService.log(
                user=actor,
                action=action,
                category=AuditLogCategory.SESSION,
                description=description,
                resource=session,
                severity=severity,
                data=data,
            )
            return
        AuditLogService.log_system(
            action=action,
            category=AuditLogCategory.SESSION,
            description=description,
            resource=session,
            severity=severity,
            data=data,
        )
