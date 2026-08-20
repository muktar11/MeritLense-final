from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from api.core.constants import CertificateStatus

from .certificate_services import generate_certificate
from .models import EvaluatorRating


def calculate_response_consistency(evaluation):
    """Return a 0-100 consistency score calculated from rule-engine results.

    Consistency is deliberately not a question pool and is never supplied by
    an evaluator.  A candidate who performs at the same level from response
    to response scores 100; variation lowers the score by the population
    standard deviation of the effective response percentages.  When the rule
    engine has no usable response result yet, return 0 so the API contract
    still always contains a numeric value.
    """
    percentages = []
    for result in evaluation.response_results.only("score", "max_score", "metadata"):
        max_score = Decimal(str(result.max_score))
        if max_score <= 0:
            continue
        effective_score = Decimal(str((result.metadata or {}).get("effective_score", result.score)))
        percentage = max(Decimal("0"), min(Decimal("100"), effective_score * Decimal("100") / max_score))
        percentages.append(percentage)

    if not percentages:
        return 0

    mean = sum(percentages, Decimal("0")) / Decimal(len(percentages))
    variance = sum(((value - mean) ** 2 for value in percentages), Decimal("0")) / Decimal(len(percentages))
    score = max(Decimal("0"), Decimal("100") - variance.sqrt())
    return int(score.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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
