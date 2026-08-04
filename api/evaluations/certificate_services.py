import base64
import hashlib
import io

from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone

from api.core.constants import EvaluationLayer
from api.sessions.models import CandidateResponse

from .models import Certificate

LAYER_LABELS = dict(EvaluationLayer.CHOICES)
LAYER_CSS_CLASS = {
    EvaluationLayer.COGNITIVE: "cognitive",
    EvaluationLayer.BEHAVIORAL: "behavioral",
    EvaluationLayer.TASK_EXECUTION: "task",
}
# Fixed display order regardless of dict iteration order - matches the
# 50/30/20 weighting order used everywhere else (Cognitive, Behavioral,
# Task Execution).
LAYER_DISPLAY_ORDER = [EvaluationLayer.COGNITIVE, EvaluationLayer.BEHAVIORAL, EvaluationLayer.TASK_EXECUTION]

# Matches the AI Evaluation Framework spec's Step 8 threshold exactly -
# this is a display/recommendation label only, not a pass/fail gate (a
# certificate is issued either way; see complete_session()).
READY_THRESHOLD = Decimal("60")

MERITLENSE_AI_VERSION = "v1.0"

# Certificate ID format ML-YYYY-NNNNNN. A get_or_create-style retry loop
# handles the (currently negligible, single-digit-per-day volume) race
# between the count() read and the unique constraint on save.
_MAX_ID_ATTEMPTS = 5


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


def _band_confidence(session):
    """High/Medium/Low, from the real stt_confidence recorded per response
    - not a computed-and-forgotten field, this is the actual speech
    clarity/transcription confidence the STT provider returned. None (not
    a fabricated 'High') if no response in this session has a confidence
    value at all."""
    values = list(
        CandidateResponse.objects.filter(session=session, stt_confidence__isnull=False)
        .values_list("stt_confidence", flat=True)
    )
    if not values:
        return None
    average = sum(values) / len(values)
    if average >= Decimal("0.85"):
        return "High"
    if average >= Decimal("0.6"):
        return "Medium"
    return "Low"


def _recommendation(overall_percentage):
    if overall_percentage >= READY_THRESHOLD:
        return "Suitable for task execution under standard working conditions with minimal supervision"
    return "Requires additional training before task execution"


def _recommendation_badge(overall_percentage):
    """Short badge label, same threshold as _recommendation() - not a
    separate judgment, just a shorter rendering of it for the score card."""
    if overall_percentage >= Decimal("80"):
        return "High Potential", "Recommended"
    if overall_percentage >= READY_THRESHOLD:
        return "Suitable", "Recommended"
    return "Development Needed", "Not Yet Recommended"


def _identity_verification_context(session):
    """Real face-match/liveness data already captured during the
    candidate's pre-interview identity check (api/sessions/identity_services.py)
    - not re-derived or estimated. None fields where verification never
    ran (e.g. this evaluation's tier doesn't require it)."""
    if session is None:
        return {"attempted": False}
    if session.verification_status in ("NOT_STARTED", ""):
        return {"attempted": False}
    return {
        "attempted": True,
        "status": session.verification_status,
        "face_match_score": float(session.face_match_score) if session.face_match_score is not None else None,
        "single_face_detected": session.single_face_detected,
    }


def _work_readiness_label(evaluation):
    # Reuses the existing readiness engine's own real output
    # (EvaluationRuleEngine, api/evaluations/rule_engine.py) rather than
    # re-deriving a second, possibly-inconsistent "job ready" judgment
    # from the score alone.
    mapping = {
        "READY": "Job Ready",
        "NOT_READY": "Not Ready",
        "PENDING": "Pending Review",
    }
    return mapping.get(evaluation.readiness_status, "Pending Review")


def _top_skill_and_improvement_area(competencies_summary):
    scored = [c for c in competencies_summary if c.get("response_count", 0) or c.get("percentage") is not None]
    if not scored:
        return None, None
    ranked = sorted(scored, key=lambda c: c.get("percentage") or 0, reverse=True)
    top = ranked[0]
    bottom = ranked[-1]
    top_label = f"{top.get('competency_name') or top.get('competency_code')}: {_qualitative_band(top.get('percentage'))}"
    if bottom is top and len(ranked) == 1:
        return top_label, None
    improvement = f"Requires improvement in {bottom.get('competency_name') or bottom.get('competency_code')}"
    return top_label, improvement


def _qualitative_band(percentage):
    if percentage is None:
        return "N/A"
    percentage = float(percentage)
    if percentage >= 85:
        return "Strong"
    if percentage >= 70:
        return "Good"
    if percentage >= 50:
        return "Fair"
    return "Needs Improvement"


def generate_certificate(evaluation, summary):
    """Builds (or regenerates) the Certificate + PDF for `evaluation`,
    given its just-computed SessionEvaluationSummary. Every field is
    either real data or an explicit N/A - see the module docstring on
    Certificate for what's intentionally not fabricated yet."""
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
    certificate.expires_at = certificate.issued_at + timezone.timedelta(days=90)

    session = evaluation.session
    layer_breakdown = summary.layer_breakdown or {}
    top_skill, improvement_area = _top_skill_and_improvement_area(summary.competencies_summary)

    verification_url = f"{settings.FRONTEND_URL}/en/verify-certificate?id={certificate.certificate_id}"

    passport = evaluation.candidate.passport_id or ""
    masked_passport = f"P****{passport[-4:]}" if len(passport) >= 4 else passport

    badge_title, badge_subtitle = _recommendation_badge(summary.overall_percentage)

    context = {
        "certificate_id": certificate.certificate_id,
        "candidate_name": evaluation.candidate.get_full_name(),
        "candidate_public_id": str(evaluation.candidate.public_id),
        "candidate_passport_masked": masked_passport,
        "role_name": session.role_name if session else evaluation.candidate.job_role,
        "final_score": float(summary.overall_percentage),
        "recommendation": _recommendation(summary.overall_percentage),
        "badge_title": badge_title,
        "badge_subtitle": badge_subtitle,
        "processing_confidence": _band_confidence(session) if session else None,
        "work_readiness": _work_readiness_label(evaluation),
        "layer_bars": [
            {
                "label": LAYER_LABELS.get(layer, layer),
                "css_class": LAYER_CSS_CLASS.get(layer, "flat"),
                "percentage": layer_breakdown[layer].get("percentage"),
            }
            for layer in LAYER_DISPLAY_ORDER
            if layer in layer_breakdown
        ],
        "has_layer_breakdown": bool(layer_breakdown),
        "competencies_summary": summary.competencies_summary,
        "top_skill": top_skill,
        "improvement_area": improvement_area,
        "identity": _identity_verification_context(session),
        # Role Fit Engine needs a post-live-interview evaluator rating that
        # doesn't exist anywhere in the codebase yet - deliberately None,
        # not fabricated, until that feature is built.
        "role_fit": None,
        "issue_date": certificate.issued_at.strftime("%Y-%m-%d"),
        "expiry_date": certificate.expires_at.strftime("%Y-%m-%d"),
        "session_id": str(session.public_id) if session else "",
        "evaluation_timestamp": (evaluation.completed_at or now).strftime("%Y-%m-%d %H:%M UTC"),
        "system_version": MERITLENSE_AI_VERSION,
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
