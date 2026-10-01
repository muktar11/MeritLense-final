def _role_display_name(role_code):
    return (role_code or "").replace("_", " ").title()


def _candidate_queryset_for_owner(owner_type, owner):
    from api.candidates.models import Candidate

    if owner_type == "COMPANY":
        return Candidate.objects.filter(company=owner)
    return Candidate.objects.filter(created_by=owner)


def _scored_summaries_for_owner(owner_type, owner):
    """A candidate's session is "comparable" once it has real scored
    competency results - EVALUATED or REQUIRES_HUMAN_REVIEW (that status
    only means a human still needs to sign off, the scoring itself already
    ran) - never PENDING/PARTIALLY_EVALUATED/EVALUATION_FAILED."""
    from api.evaluations.models import SessionEvaluationSummary

    candidates = _candidate_queryset_for_owner(owner_type, owner)
    return (
        SessionEvaluationSummary.objects
        .filter(
            candidate__in=candidates,
            status__in=[
                SessionEvaluationSummary.STATUS_EVALUATED,
                SessionEvaluationSummary.STATUS_REQUIRES_HUMAN_REVIEW,
            ],
        )
        .exclude(session__role_code="")
        .select_related("session", "candidate", "evaluation")
    )


def _current_rule_set_id(owner_type, owner, role_code):
    """The id of the ScoringRuleSet a brand-new evaluation under this role
    would be scored against today - mirrors ScoringService._resolve_rule_set's
    own "most recently created, is_active" selection (company-specific rule
    set first, falling back to a non-company one). This is what Candidate
    Comparison treats as "this role's current, compatible assessment
    version" (spec item 2): nothing in the data model automatically retires
    an older ScoringRuleSet once a newer one is added for the same role, so
    without this check two candidates scored months apart under genuinely
    different rules (different competency weighting, maybe a different
    dimension set) would silently end up side by side in one table."""
    from api.evaluations.models import ScoringRuleSet

    company = owner if owner_type == "COMPANY" else None
    queryset = ScoringRuleSet.objects.filter(role_code=role_code, is_active=True).order_by("-created_at")
    rule_set = None
    if company is not None:
        rule_set = queryset.filter(company=company).first()
    if rule_set is None:
        rule_set = queryset.first()
    return rule_set.id if rule_set else None


def _current_rule_set_ids_by_role(owner_type, owner, role_codes):
    return {role_code: _current_rule_set_id(owner_type, owner, role_code) for role_code in role_codes}


def get_comparable_roles(*, owner_type, owner):
    """Step 1 of Candidate Comparison: every role_code with at least one
    candidate whose LATEST evaluation under that role is both actually
    scored AND scored under that role's current ScoringRuleSet version -
    a candidate whose only scored attempt used a now-superseded rule set
    doesn't count here, since they would not actually be selectable in
    step 2 either (see get_eligible_candidates). Counts distinct
    candidates, not evaluations - a candidate re-assessed twice under the
    same role still counts once."""
    summaries = _scored_summaries_for_owner(owner_type, owner)
    rows = list(summaries.values("session__role_code", "candidate_id", "rule_set_id"))
    role_codes = {row["session__role_code"] for row in rows}
    current_rule_set_by_role = _current_rule_set_ids_by_role(owner_type, owner, role_codes)

    by_role = {}
    for row in rows:
        role_code = row["session__role_code"]
        if row["rule_set_id"] != current_rule_set_by_role.get(role_code):
            continue
        by_role.setdefault(role_code, set()).add(row["candidate_id"])
    return [
        {"role_code": role_code, "role_name": _role_display_name(role_code), "candidate_count": len(ids)}
        for role_code, ids in sorted(by_role.items(), key=lambda kv: kv[0])
    ]


def get_eligible_candidates(*, owner_type, owner, role_code):
    """Step 2 of Candidate Comparison: candidates with a scored evaluation
    under this exact role_code, scored under that role's CURRENT
    ScoringRuleSet - the only ones eligible to be selected for comparison
    once a role is chosen (see _current_rule_set_id for why this matters:
    a candidate's only matching evaluation might have used a since-
    superseded, incompatible version of the rules). A candidate
    re-assessed since the rules changed is still eligible, via their newer,
    current-version attempt - only the stale attempt itself is excluded."""
    current_rule_set_id = _current_rule_set_id(owner_type, owner, role_code)
    summaries = (
        _scored_summaries_for_owner(owner_type, owner)
        .filter(session__role_code=role_code, rule_set_id=current_rule_set_id)
        .order_by("-created_at")
    )
    seen = {}
    for summary in summaries:
        seen.setdefault(summary.candidate_id, summary.candidate)
    return [
        {
            "candidate_id": str(c.public_id),
            "candidate_name": c.get_full_name(),
            "job_role": c.get_job_role_display(),
        }
        for c in seen.values()
    ]


READINESS_LABELS = {
    "READY": ("Ready", "جاهز"),
    "NOT_READY": ("Readiness Gaps Identified", "توجد فجوات جاهزية"),
    "PARTIALLY_READY": ("Partially Ready", "جاهزية جزئية"),
    "INCOMPLETE": ("Insufficient Evidence", "أدلة غير كافية"),
    "PENDING": ("Pending", "قيد المراجعة"),
}


def _readiness_display(readiness_status, language):
    idx = 1 if language == "ar" else 0
    return READINESS_LABELS.get(readiness_status, READINESS_LABELS["PENDING"])[idx]


# Fixed order _build_critical_competency_status always returns its 5
# canonical dimensions in (api/reports/services.py) - zipping positionally
# against this is safe and avoids re-deriving the risk-key matching logic
# that function already owns.
DIMENSION_KEYS_IN_ORDER = ("SAFETY", "HYGIENE", "COMMUNICATION", "PRACTICAL_TASKS", "BEHAVIORAL")


def _competency_rows(report, role_code):
    """Role-scoped competency rows for the comparison table/radar chart -
    reads ONLY report.report_payload (already computed, stored, immutable
    once ACTIVE), never recalculates a score or re-decides a classification.

    Classification is Critical/Non-Critical where certificate_services'
    ROLE_COMPETENCY_CONFIG has been authored for this role (driver only,
    today); every other role shows "REQUIRED" instead of fabricating a
    Critical/Non-Critical split that was never actually decided for it.
    A dimension not required for this role is excluded (treated as N/A)
    the same way either path - via role_config's own not_applicable flag
    where it exists, or via required_dimensions_for_role as a fallback for
    roles role_config doesn't cover yet."""
    from api.evaluations.certificate_services import required_dimensions_for_role, role_competency_config

    role_config = role_competency_config(role_code)
    required = set(required_dimensions_for_role(role_code))
    items = ((report.report_payload or {}).get("critical_competency_status")) or []

    rows = []
    for dim_key, item in zip(DIMENSION_KEYS_IN_ORDER, items):
        if item.get("not_applicable"):
            continue
        if role_config is None and dim_key not in required:
            continue
        classification = role_config.get(dim_key) if role_config is not None else "REQUIRED"
        rows.append({
            "dimension_key": dim_key,
            "label": item.get("label"),
            "classification": classification,
            "percentage": float(item.get("percentage") or 0),
            "status_label": item.get("status_label"),
            "tone": item.get("tone"),
        })
    return rows


def _ensure_active_report(evaluation, actor):
    """Reads the existing ACTIVE EvaluationReport if one exists; otherwise
    generates one via the exact same on-demand path the Score Management
    page already uses (EvaluationReportService.generate_for_evaluation).
    That call FORMATS already-computed CompetencyEvaluationResult/
    ResponseEvaluationResult rows into report shape - it does not
    recompute any score, threshold, or readiness decision - so calling it
    here is reading authoritative data, not creating new analysis. Never
    regenerates an already-active report (that would mark it superseded
    for no reason every time the comparison page loads)."""
    from api.reports.models import EvaluationReport
    from api.reports.services import EvaluationReportError, EvaluationReportService

    existing = (
        EvaluationReport.objects
        .filter(evaluation=evaluation, report_status=EvaluationReport.STATUS_ACTIVE)
        .order_by("-generated_at")
        .first()
    )
    if existing is not None:
        return existing
    try:
        return EvaluationReportService.generate_for_evaluation(evaluation=evaluation, actor=actor)
    except EvaluationReportError:
        return None


def build_full_comparison(*, owner_type, owner, role_code, candidate_ids, language, actor):
    """Steps 3+4 of Candidate Comparison: Candidate Summary + role-scoped
    Competency Comparison, for 2-4 already-eligible candidates - built
    entirely from each candidate's existing (or on-demand-formatted, never
    recalculated) authoritative EvaluationReport. See this module's other
    docstrings for the "never recalculate" guardrail this serves.

    Re-checks the same current-ScoringRuleSet requirement
    get_eligible_candidates already applies (rather than trusting the
    candidate_ids the caller selected) - this is the actual comparison
    output, so it's the last line of defense against ever placing two
    candidates scored under incompatible rule versions in one table,
    even if eligibility shifted between selection and this call."""
    current_rule_set_id = _current_rule_set_id(owner_type, owner, role_code)
    candidates = _candidate_queryset_for_owner(owner_type, owner).filter(public_id__in=candidate_ids)
    by_id = {str(c.public_id): c for c in candidates}

    entries = []
    for candidate_id in candidate_ids:
        candidate = by_id.get(candidate_id)
        if candidate is None:
            continue
        summary = (
            _scored_summaries_for_owner(owner_type, owner)
            .filter(candidate=candidate, session__role_code=role_code, rule_set_id=current_rule_set_id)
            .order_by("-created_at")
            .first()
        )
        if summary is None:
            continue
        report = _ensure_active_report(summary.evaluation, actor)
        if report is None:
            continue

        assessment_context = (report.report_payload or {}).get("assessment_context") or {}
        assessed = assessment_context.get("competencies_assessed_count") or 0
        required = assessment_context.get("competencies_required_count") or 0
        coverage_pct = round((assessed / required) * 100) if required else 0

        entries.append({
            "candidate_id": str(candidate.public_id),
            "candidate_name": candidate.get_full_name(),
            "evaluation_id": str(summary.evaluation.public_id),
            "readiness_status": report.readiness_status,
            "readiness_display": _readiness_display(report.readiness_status, language),
            "assessment_coverage": coverage_pct,
            "requires_human_review": bool(report.requires_human_review),
            "competencies": _competency_rows(report, role_code),
        })
    return entries


def compute_key_differences(entries, language="en", max_items=3):
    """Factual, descriptive differences only - the highest/lowest scorer
    per competency and the point spread between them. Never ranks
    candidates overall, never picks a "winner" - purely restates the same
    numbers already in the comparison table, prioritizing Critical/
    Required competencies and the largest spreads, per spec item 5."""
    if len(entries) < 2:
        return []

    by_label = {}
    for entry in entries:
        for row in entry["competencies"]:
            by_label.setdefault(row["label"], {"classification": row["classification"], "scores": []})
            by_label[row["label"]]["scores"].append((entry["candidate_name"], row["percentage"]))

    scored = []
    for label, info in by_label.items():
        scores = info["scores"]
        if len(scores) < 2:
            continue
        best_name, best_pct = max(scores, key=lambda s: s[1])
        worst_name, worst_pct = min(scores, key=lambda s: s[1])
        spread = best_pct - worst_pct
        if spread <= 0:
            continue
        is_priority = info["classification"] in ("CRITICAL", "REQUIRED")
        scored.append((is_priority, spread, label, best_name, best_pct, worst_name, worst_pct))

    # Critical/Required competencies first, then by widest spread.
    scored.sort(key=lambda row: (not row[0], -row[1]))

    differences = []
    for _, spread, label, best_name, best_pct, worst_name, worst_pct in scored[:max_items]:
        if language == "ar":
            text = f"{best_name} حقق أعلى نتيجة في {label} ({best_pct:.0f}%)، بينما حقق {worst_name} أدنى نتيجة ({worst_pct:.0f}%)."
        else:
            text = f"{best_name} scored highest in {label} ({best_pct:.0f}%), while {worst_name} scored lowest ({worst_pct:.0f}%)."
        differences.append({"label": label, "text": text})
    return differences


def build_candidate_comparison_entry(candidate, language="en"):
    """One candidate's row for the multi-candidate comparison view.

    Reads api.evaluations.models.SessionEvaluationSummary - the real,
    currently-populated scoring pipeline output - not the legacy
    api.scores ScoreSet/CandidateScore models, which nothing in the actual
    scoring pipeline writes to. Competency labels are humanized via
    EvaluationReportService._friendly_competency_name, the same mapping
    already used for the internal evaluation report, so a candidate's
    "unmapped" competency reads as "Overall Workforce Readiness" here too
    rather than as a raw internal code.

    `language` is the viewer's own dashboard locale (this compares
    candidates who may have taken their interview in different languages,
    so there's no single "the candidate's language" to fall back to here
    the way the certificate/report generators do) - passed through by the
    view from the frontend's current next-intl locale.
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
                item.get('competency_code'), item.get('competency_name'), language=language
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
