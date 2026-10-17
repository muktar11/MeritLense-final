from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from api.core.constants import CertificateStatus

from .certificate_services import generate_certificate
from .models import EvaluatorRating, EvaluatorRatingCorrection


SCORE_FIELDS = ("safety_awareness", "hygiene", "communication", "task_execution")


def calculate_evaluator_outcome(ratings):
    """Apply the evaluator-approved four-field average and readiness bands."""
    values = [Decimal(str(ratings[field])) for field in SCORE_FIELDS]
    score = (sum(values, Decimal("0")) / Decimal(len(values))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if score < Decimal("40"):
        readiness = "NOT_READY"
    elif score < Decimal("60"):
        readiness = "PARTIALLY_READY"
    else:
        readiness = "READY"
    return score, readiness


def _rating_values(rating):
    return {
        "safety_awareness": rating.safety_awareness,
        "hygiene": rating.hygiene,
        "communication": rating.communication,
        "task_execution": rating.task_execution,
        "behavior_integrity": rating.behavior_integrity,
        "comments": rating.comments,
    }


def _apply_evaluator_outcome(evaluation, rating):
    score, readiness = calculate_evaluator_outcome(_rating_values(rating))
    evaluation.score = score
    evaluation.readiness_status = readiness
    evaluation.readiness_override_applied = False
    evaluation.readiness_override_reason = ""
    evaluation.save(update_fields=[
        "score", "readiness_status", "readiness_override_applied",
        "readiness_override_reason", "updated_at",
    ])
    return score, readiness


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
    """Store a single final rating. Any later change must use correction flow."""
    if EvaluatorRating.objects.filter(evaluation=evaluation).exists():
        raise ValueError("The evaluator rating is final. An authorized correction is required to change it.")
    rating = EvaluatorRating.objects.create(
        evaluation=evaluation,
        **ratings,
        rated_by=actor,
        rated_at=timezone.now(),
    )
    scheduled = bool(evaluation.session_id and evaluation.session.is_scheduled_interview)
    if scheduled:
        _apply_evaluator_outcome(evaluation, rating)
        from api.reports.services import EvaluationReportService
        EvaluationReportService.generate_evaluator_report(evaluation=evaluation, actor=actor)
    if evaluation.certificate_status == CertificateStatus.ISSUED:
        summary = evaluation.session_summaries.select_related("rule_set").first()
        if summary is not None:
            generate_certificate(evaluation, summary)
    return rating, True


def correct_evaluator_rating(evaluation, *, ratings, reason, actor):
    """Authorized correction with immutable before/after audit data."""
    rating = EvaluatorRating.objects.get(evaluation=evaluation)
    previous_values = _rating_values(rating)
    corrected_values = {**previous_values, **ratings}
    EvaluatorRatingCorrection.objects.create(
        evaluation=evaluation,
        rating=rating,
        previous_values=previous_values,
        corrected_values=corrected_values,
        reason=reason.strip(),
        corrected_by=actor,
    )
    EvaluatorRating.objects.filter(pk=rating.pk).update(
        **ratings,
        rated_by=actor,
        rated_at=timezone.now(),
        updated_at=timezone.now(),
    )
    rating.refresh_from_db()
    scheduled = bool(evaluation.session_id and evaluation.session.is_scheduled_interview)
    if scheduled:
        _apply_evaluator_outcome(evaluation, rating)
        from api.reports.services import EvaluationReportService
        EvaluationReportService.generate_evaluator_report(evaluation=evaluation, actor=actor)

    if evaluation.certificate_status == CertificateStatus.ISSUED:
        summary = evaluation.session_summaries.select_related("rule_set").first()
        if summary is not None:
            try:
                generate_certificate(evaluation, summary)
            except Exception:
                pass
    return rating
