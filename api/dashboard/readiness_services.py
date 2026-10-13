from django.db.models import BooleanField, Count, Exists, ExpressionWrapper, OuterRef, Q
from django.db.models.functions import Coalesce

from api.core.constants import CandidateJobRoles, EvaluationStatus, ReadinessStatus
from api.reports.models import EvaluationReport


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


REASON_HUMAN_REVIEW = 'HUMAN_REVIEW'
REASON_INSUFFICIENT_EVIDENCE = 'INSUFFICIENT_EVIDENCE'


def parse_attention_limit(request, default=5):
    try:
        return max(1, min(int(request.query_params.get('limit', default)), 50))
    except ValueError:
        return default


def requires_attention(evaluations, limit=5):
    """Decision-oriented queue payload: completed evaluations the owner
    should look at next - the scoring engine flagged them for human review
    (and no readiness correction has been recorded since), or there wasn't
    enough evidence to reach any readiness decision.

    `evaluations` is the caller's already-scoped queryset (a company's or
    a B2C user's)."""
    flagged_report = EvaluationReport.objects.filter(
        evaluation=OuterRef('pk'),
        report_status=EvaluationReport.STATUS_ACTIVE,
        requires_human_review=True,
    )
    needs_review = Q(Exists(flagged_report)) & Q(readiness_correction__isnull=True)
    insufficient = Q(current_readiness=ReadinessStatus.INCOMPLETE)
    queryset = (
        completed_with_current_readiness(evaluations)
        .filter(needs_review | insufficient)
        .annotate(
            needs_review=ExpressionWrapper(needs_review, output_field=BooleanField()),
            activity_at=Coalesce('last_evaluation_date', 'updated_at'),
        )
    )
    counts = queryset.aggregate(
        total=Count('id'),
        human_review=Count('id', filter=needs_review),
        insufficient_evidence=Count('id', filter=insufficient),
    )

    role_dict = dict(CandidateJobRoles.CHOICES)
    items = []
    for evaluation in queryset.select_related('candidate').order_by('-activity_at')[:limit]:
        reasons = []
        if evaluation.needs_review:
            reasons.append(REASON_HUMAN_REVIEW)
        if evaluation.current_readiness == ReadinessStatus.INCOMPLETE:
            reasons.append(REASON_INSUFFICIENT_EVIDENCE)
        name = f"{evaluation.candidate_first_name} {evaluation.candidate_last_name}".strip()
        items.append({
            'evaluation_id': str(evaluation.public_id),
            'candidate_name': name or (evaluation.candidate.get_full_name() if evaluation.candidate else ''),
            'job_role': evaluation.candidate_job_role,
            'job_role_display': role_dict.get(evaluation.candidate_job_role, evaluation.candidate_job_role),
            'readiness_status': evaluation.current_readiness,
            # Withheld while awaiting human review (release safeguard).
            'score': float(evaluation.score) if evaluation.score is not None and not evaluation.is_held_for_review else None,
            'reasons': reasons,
            'activity_at': evaluation.activity_at,
        })
    return {**counts, 'items': items}
