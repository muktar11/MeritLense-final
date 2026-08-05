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

# Certificate ID format ML-YYYY-NNNNNN. A get_or_create-style retry loop
# handles the (currently negligible, single-digit-per-day volume) race
# between the count() read and the unique constraint on save.
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


def _generate_certificate_id():
    year = timezone.now().year
    prefix = f"ML-{year}-"
    existing = Certificate.objects.filter(certificate_id__startswith=prefix).count()
    return f"{prefix}{existing + 1:06d}"


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


def _role_profile_version(session):
    """Same version string the internal evaluation report uses (role code
    + question set version) - see EvaluationReportService._derive_role_profile_version."""
    from api.reports.services import EvaluationReportService

    if session is None:
        return "N/A"
    return EvaluationReportService._derive_role_profile_version(session)


def generate_certificate(evaluation, summary):
    """Builds (or regenerates) the Certificate + PDF for `evaluation`,
    given its just-computed SessionEvaluationSummary. This is a
    presentable completion certificate, not the internal evaluation
    report - it states a real readiness classification (from the same
    rule engine as the internal report), but none of the raw score,
    per-competency/per-layer breakdown, or identity-check data that
    belongs in that separate, more detailed report instead."""
    from weasyprint import HTML

    certificate, created = Certificate.objects.get_or_create(
        evaluation=evaluation,
        defaults={"candidate": evaluation.candidate},
    )
    if not certificate.certificate_id:
        for _ in range(_MAX_ID_ATTEMPTS):
            candidate_id = _generate_certificate_id()
            if not Certificate.objects.filter(certificate_id=candidate_id).exclude(pk=certificate.pk).exists():
                certificate.certificate_id = candidate_id
                break
        else:
            raise CertificateGenerationError("Could not allocate a unique certificate ID")

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
        "assessment_id": str(evaluation.public_id),
        "system_version": READINESS_FRAMEWORK_VERSION,
        "verification_url": verification_url,
        "qr_data_uri": _build_qr_data_uri(verification_url),
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
    }

    html_string = render_to_string("evaluations/certificate.html", context)
    pdf_bytes = HTML(string=html_string).write_pdf()

    filename = f"{certificate.certificate_id}.pdf"
    certificate.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
    certificate.pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
    certificate.save()
    return certificate
