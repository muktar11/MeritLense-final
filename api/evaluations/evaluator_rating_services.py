from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from api.core.constants import CertificateStatus

from .certificate_services import generate_certificate
from .models import EvaluatorRating


def _consistency_from_percentages(percentages):
    mean = sum(percentages, Decimal("0")) / Decimal(len(percentages))
    variance = sum(((value - mean) ** 2 for value in percentages), Decimal("0")) / Decimal(len(percentages))
    score = max(Decimal("0"), Decimal("100") - variance.sqrt())
    return int(score.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_response_consistency(evaluation, *, fallback_rating=None):
    """Return a 0-100 consistency score calculated from rule-engine results.

    Consistency is deliberately not a question pool and is never supplied by
    an evaluator.  A candidate who performs at the same level from response
    to response scores 100; variation lowers the score by the population
    standard deviation of the effective response percentages.  When the rule
    engine has at least two usable response results those are authoritative.
    Historical live calls did not persist their transcripts, so for those
    records use dispersion across the five evaluator-assessed approved
    dimensions rather than presenting a fabricated zero.
    """
    percentages = []
    for result in evaluation.response_results.only("score", "max_score", "metadata"):
        max_score = Decimal(str(result.max_score))
        if max_score <= 0:
            continue
        effective_score = Decimal(str((result.metadata or {}).get("effective_score", result.score)))
        percentage = max(Decimal("0"), min(Decimal("100"), effective_score * Decimal("100") / max_score))
        percentages.append(percentage)

    if len(percentages) >= 2:
        return _consistency_from_percentages(percentages)

    if fallback_rating is not None:
        # hygiene/communication are None on any rating submitted before
        # those dimensions were added (see EvaluatorRating's docstring) -
        # skip rather than crash on Decimal(str(None)).
        fallback_values = [
            fallback_rating.safety_awareness,
            fallback_rating.hygiene,
            fallback_rating.communication,
            fallback_rating.behavior_integrity,
            fallback_rating.task_execution,
        ]
        fallback_percentages = [Decimal(str(value)) for value in fallback_values if value is not None]
        if len(fallback_percentages) >= 2:
            return _consistency_from_percentages(fallback_percentages)

    return 0


# Mirrors EvaluationReportService._risk_block's own thresholds (see
# api/reports/services.py) - a score below 40 is High risk, up to 70 is
# Medium, above that is Low - so the evaluator-entered Behavioral
# Indicators rating buckets the same way the AI-led competency risk
# indicators already do, rather than inventing a second scale.
def behavioral_risk_level(behavior_integrity_score):
    """Per Report Specification Section 4: Behavioral Indicators is shown
    as an evidence-based Risk Indicator (High/Medium/Low), never as a raw
    0-100 score, on any candidate/employer-facing surface (certificate,
    report). The raw score itself is still stored and used in scoring -
    this only controls how it's presented."""
    if behavior_integrity_score is None:
        return "Not Assessed"
    if behavior_integrity_score < 40:
        return "High"
    if behavior_integrity_score <= 70:
        return "Medium"
    return "Low"


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
