from django.utils import timezone

from api.core.constants import CertificateStatus

from .certificate_services import generate_certificate
from .models import EvaluatorRating


def submit_evaluator_rating(evaluation, *, ratings, actor):
    """Creates or updates the single EvaluatorRating row for `evaluation`.
    If a certificate was already issued, regenerates it so the PDF picks
    up the new ratings - safe to re-call (generate_certificate is
    idempotent) and non-blocking (a PDF failure here must not prevent the
    rating itself from saving, matching complete_session()'s contract for
    scoring/certificate side effects)."""
    rating, created = EvaluatorRating.objects.update_or_create(
        evaluation=evaluation,
        defaults={**ratings, "rated_by": actor, "rated_at": timezone.now()},
    )

    if evaluation.certificate_status == CertificateStatus.ISSUED:
        summary = evaluation.session_summaries.select_related("rule_set").first()
        if summary is not None:
            try:
                generate_certificate(evaluation, summary)
            except Exception:
                pass

    return rating, created
