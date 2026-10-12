from django.db.models import Count
from django.db.models.functions import Coalesce

from api.core.constants import EvaluationStatus, ReadinessStatus


def completed_with_current_readiness(evaluations):
    """Completed evaluations that carry a readiness result, annotated with
    the readiness as it stands today - an EvaluationReadinessCorrection
    supersedes the original decision (see
    ReadinessRecordService.current_readiness_status), done in SQL here so
    the dashboard doesn't run a query per evaluation.

    `evaluations` is the caller's already-scoped queryset (a company's, a
    B2C user's, or the whole platform's for admins)."""
    return evaluations.filter(
        status=EvaluationStatus.COMPLETED,
        readiness_indicator_enabled=True,
    ).annotate(
        current_readiness=Coalesce(
            'readiness_correction__corrected_readiness_status', 'readiness_status'
        ),
    )


def readiness_distribution(evaluations):
    """Overall Readiness Index payload: completed evaluations by their
    actual readiness outcome (not by evaluation status), plus the ready
    rate among evaluations that reached a decision."""
    counts = dict(
        completed_with_current_readiness(evaluations)
        .values('current_readiness')
        .annotate(n=Count('id'))
        .values_list('current_readiness', 'n')
    )
    total = sum(counts.values())
    distribution = [
        {
            'status': status,
            'status_display': display,
            'count': counts.get(status, 0),
            'percentage': round(counts.get(status, 0) / total * 100, 2) if total else 0,
        }
        for status, display in ReadinessStatus.CHOICES
    ]
    decided = sum(
        counts.get(s, 0)
        for s in (ReadinessStatus.READY, ReadinessStatus.PARTIALLY_READY, ReadinessStatus.NOT_READY)
    )
    ready_rate = round(counts.get(ReadinessStatus.READY, 0) / decided * 100, 2) if decided else None
    return {'total': total, 'ready_rate': ready_rate, 'distribution': distribution}
