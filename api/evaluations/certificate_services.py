import base64
import hashlib
import io
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Certificate
from .readiness_record_services import EvaluationReadinessRecordService

READINESS_FRAMEWORK_VERSION = "v1.0"

_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "meritlense-logo.png"
_logo_data_uri_cache = None


def _logo_data_uri():
    """The real MeritLense logo (not a hand-drawn approximation), read once
    per process and cached - it's a fixed asset, not per-certificate data."""
    global _logo_data_uri_cache
    if _logo_data_uri_cache is None:
        encoded = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
        _logo_data_uri_cache = f"data:image/png;base64,{encoded}"
    return _logo_data_uri_cache

# certificate_id (ML-YYYY-NNNNNN) and assessment_id (ASM-YYYY-NNNNNN) are
# deliberately two separate counters on two separate fields - the
# certificate identifies the issued document, the assessment ID identifies
# the underlying completed assessment record, and neither is derived from
# the other. A get_or_create-style retry loop handles the (currently
# negligible, single-digit-per-day volume) race between the count() read
# and the unique constraint on save.
_MAX_ID_ATTEMPTS = 5

# Mirrors EvaluationReportService._resolve_readiness_indicator's own three
# levels - the same real, already-computed readiness classification used
# in the internal evaluation report, not a second judgment re-derived from
# the raw score. "position" drives the readiness badge's color (1 = red,
# 3 = green).
READINESS_GAUGE = {
    "NOT_READY": {"label": "Readiness Gaps Identified", "position": 1},
    "PARTIALLY_READY": {"label": "Partially Ready", "position": 2},
    "READY": {"label": "Ready", "position": 3},
}


class CertificateGenerationError(Exception):
    pass


def _generate_sequential_id(field_name, prefix):
    year = timezone.now().year
    full_prefix = f"{prefix}-{year}-"
    existing = Certificate.objects.filter(**{f"{field_name}__startswith": full_prefix}).count()
    return f"{full_prefix}{existing + 1:06d}"


def _assign_unique_id(certificate, field_name, prefix):
    if getattr(certificate, field_name):
        return
    for _ in range(_MAX_ID_ATTEMPTS):
        value = _generate_sequential_id(field_name, prefix)
        if not Certificate.objects.filter(**{field_name: value}).exclude(pk=certificate.pk).exists():
            setattr(certificate, field_name, value)
            return
    raise CertificateGenerationError(f"Could not allocate a unique {field_name}")


def _build_qr_data_uri(verification_url):
    import qrcode

    img = qrcode.make(verification_url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"


def _candidate_photo_data_uri(candidate):
    """A plain portrait for the certificate - the candidate's own uploaded
    photo (see the registration flow's photo-upload + passport-match
    check), not a face-match crop or verification artifact. None if the
    candidate never uploaded one, rather than substituting some other image
    and implying it's the same thing."""
    if not candidate.profile_photo:
        return None
    try:
        candidate.profile_photo.open("rb")
        content = candidate.profile_photo.read()
    finally:
        candidate.profile_photo.close()
    encoded = base64.b64encode(content).decode()
    return f"data:image/jpeg;base64,{encoded}"


def _readiness_gauge_context(evaluation):
    """Reuses EvaluationReportService's own readiness classification (the
    same READY/PARTIALLY_READY/NOT_READY judgment already computed for the
    internal evaluation report - see api/reports/services.py) rather than
    re-deriving a second, possibly-inconsistent judgment from the score."""
    from api.reports.services import EvaluationReportService

    readiness_record = EvaluationReadinessRecordService.get_existing(evaluation)
    indicator = EvaluationReportService._resolve_readiness_indicator(evaluation, readiness_record)
    gauge = READINESS_GAUGE.get(indicator["code"], READINESS_GAUGE["PARTIALLY_READY"])
    return {"label": gauge["label"], "position": gauge["position"]}


def _evaluator_rating_context(evaluation):
    """The evaluator's manual 0-100 ratings on the 4 fixed pools, if any
    have been submitted (see EvaluatorRating in models.py) - purely
    additional display data, never a factor in certificate_eligibility()."""
    rating = getattr(evaluation, "evaluator_rating", None)
    if rating is None:
        return None
    from .evaluator_rating_services import calculate_response_consistency

    return {
        "safety_awareness": rating.safety_awareness,
        "behavior_integrity": rating.behavior_integrity,
        "psych_professional": rating.psych_professional,
        "task_execution": rating.task_execution,
        "consistency": calculate_response_consistency(evaluation, fallback_rating=rating),
    }


def _role_profile_version(session):
    """Same version string the internal evaluation report uses (role code
    + question set version) - see EvaluationReportService._derive_role_profile_version."""
    from api.reports.services import EvaluationReportService

    if session is None:
        return "N/A"
    return EvaluationReportService._derive_role_profile_version(session)


def certificate_eligibility(evaluation, summary):
    """Certificate issuance decision, per the MeritLense flow:
    Assessment -> Verification/Completion Checks -> Assessment Logic ->
    Readiness Level -> Report -> Certificate Decision.

    Ready / Partially Ready -> eligible (Report + Certificate).
    Not Ready -> not eligible (Report only - training/re-assessment path).
    Incomplete assessment or failed/missing identity verification ->
    not eligible regardless of readiness (Preliminary/Incomplete Report
    only) - a certificate must never be issued on data that isn't both
    real and complete.

    Returns (eligible: bool, reason: str) - reason is one of "READY",
    "PARTIALLY_READY" (both eligible), or "NOT_READY", "INCOMPLETE",
    "VERIFICATION_FAILED" (not eligible), for audit logging.
    """
    from api.reports.services import EvaluationReportService

    readiness_record = EvaluationReadinessRecordService.get_existing(evaluation)
    indicator = EvaluationReportService._resolve_readiness_indicator(evaluation, readiness_record)
    assessment_completeness = EvaluationReportService._derive_assessment_completeness(summary)
    session = evaluation.session
    identity_verified = bool(session and session.identity_verified)

    if not identity_verified:
        return False, "VERIFICATION_FAILED"
    if assessment_completeness < 100:
        return False, "INCOMPLETE"
    if indicator["code"] == "NOT_READY":
        return False, "NOT_READY"
    return True, indicator["code"]


def generate_certificate(evaluation, summary):
    """Builds (or regenerates) the Certificate + PDF for `evaluation`,
    given its just-computed SessionEvaluationSummary. This is a
    presentable completion certificate, not the internal evaluation
    report - it states a real readiness classification (from the same
    rule engine as the internal report), but none of the raw score,
    per-competency/per-layer breakdown, or identity-check data that
    belongs in that separate, more detailed report instead.

    Returns None (and marks the evaluation NOT_ISSUED) without producing
    a PDF when the candidate isn't eligible per certificate_eligibility -
    the internal report is generated independently of this and is not
    gated the same way, since a Not Ready/incomplete candidate still gets
    a report (with a training/re-assessment recommendation), just no
    certificate."""
    from api.core.constants import CertificateStatus

    eligible, reason = certificate_eligibility(evaluation, summary)
    if not eligible:
        evaluation.certificate_status = CertificateStatus.NOT_ISSUED
        evaluation.save(update_fields=["certificate_status"])
        return None

    from weasyprint import HTML

    certificate, created = Certificate.objects.get_or_create(
        evaluation=evaluation,
        defaults={"candidate": evaluation.candidate},
    )
    _assign_unique_id(certificate, "certificate_id", "ML")
    _assign_unique_id(certificate, "assessment_id", "ASM")

    now = timezone.now()
    certificate.issued_at = certificate.issued_at or now
    # This certificate does not expire - cleared explicitly so regenerating
    # an older certificate (created before this policy) drops any
    # previously-set expiry rather than leaving it stale.
    certificate.expires_at = None

    session = evaluation.session
    verification_url = f"{settings.FRONTEND_URL}/en/verify-certificate?id={certificate.certificate_id}"
    readiness = _readiness_gauge_context(evaluation)

    context = {
        "logo_data_uri": _logo_data_uri(),
        "certificate_id": certificate.certificate_id,
        "candidate_name": evaluation.candidate.get_full_name().title(),
        "role_name": session.role_name if session else evaluation.candidate.job_role,
        "role_profile_version": _role_profile_version(session),
        "candidate_photo_data_uri": _candidate_photo_data_uri(evaluation.candidate),
        "readiness_label": readiness["label"],
        "readiness_position": readiness["position"],
        "issue_date": certificate.issued_at.strftime("%Y-%m-%d"),
        "assessment_date": (evaluation.completed_at or now).strftime("%Y-%m-%d"),
        "assessment_id": certificate.assessment_id,
        "system_version": READINESS_FRAMEWORK_VERSION,
        "verification_url": verification_url,
        "qr_data_uri": _build_qr_data_uri(verification_url),
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "evaluator_rating": _evaluator_rating_context(evaluation),
    }

    html_string = render_to_string("evaluations/certificate.html", context)
    pdf_bytes = HTML(string=html_string).write_pdf()

    filename = f"{certificate.certificate_id}.pdf"
    certificate.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
    certificate.pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
    certificate.save()

    evaluation.certificate_status = CertificateStatus.ISSUED
    evaluation.certificate_issued_at = certificate.issued_at
    evaluation.save(update_fields=["certificate_status", "certificate_issued_at"])

    return certificate
