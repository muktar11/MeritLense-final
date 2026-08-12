def build_candidate_comparison_entry(candidate):
    """One candidate's row for the multi-candidate comparison view.

    Reads api.evaluations.models.SessionEvaluationSummary - the real,
    currently-populated scoring pipeline output - not the legacy
    api.scores ScoreSet/CandidateScore models, which nothing in the actual
    scoring pipeline writes to. Competency labels are humanized via
    EvaluationReportService._friendly_competency_name, the same mapping
    already used for the internal evaluation report, so a candidate's
    "unmapped" competency reads as "Overall Workforce Readiness" here too
    rather than as a raw internal code.
    """
    from api.evaluations.models import SessionEvaluationSummary
    from api.reports.services import EvaluationReportService

    latest_summary = (
        SessionEvaluationSummary.objects.filter(candidate=candidate)
        .order_by('-created_at')
        .first()
    )

    if latest_summary is not None and latest_summary.overall_percentage is not None:
        scores_by_area = {}
        for item in latest_summary.competencies_summary or []:
            label = EvaluationReportService._friendly_competency_name(
                item.get('competency_code'), item.get('competency_name')
            )
            percentage = item.get('percentage')
            if label and percentage is not None:
                scores_by_area[label] = float(percentage)

        return {
            'candidate_id': str(candidate.public_id),
            'candidate_name': candidate.get_full_name(),
            'job_role': candidate.get_job_role_display(),
            'average_score': float(latest_summary.overall_percentage),
            'scores_by_area': scores_by_area,
        }

    return {
        'candidate_id': str(candidate.public_id),
        'candidate_name': candidate.get_full_name(),
        'job_role': candidate.get_job_role_display(),
        'average_score': 0,
        'scores_by_area': {},
    }
