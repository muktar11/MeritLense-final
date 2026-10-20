from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, EvaluationLayer, ReadinessStatus
from api.questions.skill_tags import normalize_skill_code, normalize_skill_tag
from api.sessions.models import CandidateResponse
from api.translation.models import EvaluationInputArtifact

from .certificate_services import (
    assessed_dimensions,
    below_threshold_dimensions,
    evaluate_role_competency_readiness,
    minimum_required_dimensions_for_role,
    required_dimensions_for_role,
)
from . import rubric_specs
from .readiness_record_services import EvaluationReadinessRecordService
from .models import (
    CompetencyEvaluationResult,
    Evaluation,
    ResponseEvaluationResult,
    ScoringRule,
    ScoringRuleSet,
    SessionEvaluationSummary,
)


class Week6ScoringError(Exception):
    pass


class Week6ScoringService:
    """Rule Engine scoring (rulebook SCR-, CAL- and OUT- rules; status of
    each in docs/ASSESSMENT_RULES.md)."""

    DECIMAL_ZERO = Decimal("0.00")
    DECIMAL_HUNDRED = Decimal("100.00")
    DEFAULT_COMPETENCY_PASS_PERCENTAGE = Decimal("60.00")

    @classmethod
    @transaction.atomic
    def run_for_evaluation(cls, *, evaluation, actor=None, rule_set=None):
        session = getattr(evaluation, "session", None)
        if session is None:
            raise Week6ScoringError("Evaluation must be linked to an interview session before scoring")

        selected_rule_set = rule_set or cls._resolve_rule_set(evaluation)
        all_responses = list(
            CandidateResponse.objects.select_related(
                "question",
                "question__question_template",
                "evaluation_input_artifact",
            )
            .filter(session=session)
            .order_by("question__question_order", "-attempt_number", "-created_at")
        )
        # Attempt isolation: if a question were ever answered more than
        # once (CandidateResponse.attempt_number), only its latest attempt
        # may feed scoring - every response for a session gets its own
        # ResponseEvaluationResult row keyed by response id, so without
        # this dedup, _aggregate_competencies would silently sum multiple
        # attempts into one competency total. No retry path exists in the
        # app today (every response is attempt_number=1), so this is
        # currently a no-op - it exists so scoring can't silently combine
        # attempts the moment one does.
        responses = []
        seen_question_ids = set()
        for response in all_responses:
            if response.question_id in seen_question_ids:
                continue
            seen_question_ids.add(response.question_id)
            responses.append(response)
        responses.sort(key=lambda r: (r.question.question_order, r.created_at))
        if not responses:
            raise Week6ScoringError("No candidate responses are available for scoring")

        results = []
        rubric_pending = []
        for response in responses:
            result = cls._score_response(
                evaluation=evaluation,
                response=response,
                rule_set=selected_rule_set,
                pending=rubric_pending,
            )
            if result is not None:
                results.append(result)
        competency_results = cls._aggregate_competencies(
            evaluation=evaluation,
            rule_set=selected_rule_set,
            responses=responses,
            response_results=results,
        )
        # CAL-03: reconcile every assigned question to a response state, so
        # a question with no response row cannot silently drop out of the
        # result. Without a recorded skip, an unanswered question is
        # non-completion, not a zero.
        unanswered_questions = list(
            session.questions.exclude(id__in=seen_question_ids).order_by("question_order")
        )
        summary = cls._build_session_summary(
            evaluation=evaluation,
            rule_set=selected_rule_set,
            responses=responses,
            response_results=results,
            competency_results=competency_results,
            unanswered_questions=unanswered_questions,
            rubric_pending=rubric_pending,
        )
        cls._apply_evaluation_rollups(evaluation=evaluation, summary=summary, actor=actor)
        # Release safeguard: anything that needs a person's judgement is held
        # from the employer until a reviewer records a decision.
        from .human_review_services import HumanReviewService

        HumanReviewService.flag_after_scoring(evaluation, summary)
        cls._log_scoring_events(
            actor=actor,
            evaluation=evaluation,
            rule_set=selected_rule_set,
            response_results=results,
            competency_results=competency_results,
            summary=summary,
        )
        return summary

    @classmethod
    def _resolve_versioned_rule_set(cls, evaluation, session):
        """The rule set built for the session's own question-set version.

        Not filtered on is_active: a session created while its version was
        live is scored by that version's rules even after a rollback
        deactivates them. Company-specific first, as in legacy resolution."""
        version = session.question_set_version
        if not version:
            return None
        queryset = ScoringRuleSet.objects.filter(
            question_set_version=version,
            evaluation_tier=evaluation.evaluation_tier,
            role_code=session.role_code,
        ).order_by("-created_at")
        if evaluation.company_id:
            company_rule_set = queryset.filter(company=evaluation.company).first()
            if company_rule_set is not None:
                return company_rule_set
        return queryset.filter(company__isnull=True).first()

    @classmethod
    def _resolve_rule_set(cls, evaluation):
        # Authoritative-snapshot fix: once an evaluation has been scored
        # against a rule set, that rule set is pinned on the evaluation and
        # always reused - never re-resolved by "newest for this
        # role/tier/company" again. Without this, a rule set activated
        # after the candidate's interview already ran could silently
        # become what a re-score (retry, admin "run scoring", or
        # `regenerate_report`) scores that historical evaluation against.
        if evaluation.scoring_rule_set_id:
            return evaluation.scoring_rule_set

        session = evaluation.session
        resolved = cls._resolve_versioned_rule_set(evaluation, session)
        if resolved is not None:
            evaluation.scoring_rule_set = resolved
            evaluation.save(update_fields=["scoring_rule_set"])
            return resolved

        # Legacy resolution for sessions whose question-set version has no
        # dedicated rule set. Versioned rule sets are excluded so an
        # assessment never picks up another version's scoring rules - e.g. a
        # GOV1.2 interview still in progress when GOV2.0 is activated.
        queryset = ScoringRuleSet.objects.filter(
            evaluation_tier=evaluation.evaluation_tier,
            is_active=True,
            question_set_version="",
        ).order_by("-created_at")
        resolved = None
        if evaluation.company_id:
            company_queryset = queryset.filter(company=evaluation.company)
            if session.role_code:
                resolved = company_queryset.filter(role_code=session.role_code).first()
            if resolved is None:
                resolved = company_queryset.filter(role_code="").first()
        if resolved is None and session.role_code:
            resolved = queryset.filter(role_code=session.role_code).first()
        if resolved is None:
            resolved = queryset.filter(role_code="").first()
        if resolved is None:
            raise Week6ScoringError("No active scoring rule set matches this evaluation")

        evaluation.scoring_rule_set = resolved
        evaluation.save(update_fields=["scoring_rule_set"])
        return resolved

    @classmethod
    def _score_response(cls, *, evaluation, response, rule_set, pending=None):
        artifact = getattr(response, "evaluation_input_artifact", None)
        # Without a prepared evidence artifact, there is no defensible basis
        # for a score. Leave this response out of scoring; the session rollup
        # will mark it incomplete instead of manufacturing a zero.
        if artifact is None:
            return None
        if artifact.metadata.get("score_evidence_reliable") is False:
            # The interpreter marked the evidence uncertain. It cannot be
            # converted into either a pass or a zero; leave this response
            # incomplete until evidence is reliable.
            return None
        rule = cls._resolve_rule(rule_set=rule_set, response=response, artifact=artifact)
        if rule is None:
            if artifact is None:
                return None
            raise Week6ScoringError(f"No scoring rule found for response {response.public_id}")

        observed = cls._normalize_indicator_list(getattr(artifact, "observed_indicators", []))
        expected = cls._normalize_indicator_list(rule.expected_indicators)
        required = cls._normalize_indicator_list(rule.required_indicators)
        critical_failure_indicators = cls._normalize_indicator_list(rule.critical_failure_indicators)
        artifact_risk_flags = cls._normalize_indicator_list(getattr(artifact, "risk_flags", []))
        rule_risk_flags = cls._normalize_indicator_list(rule.risk_flags)
        weighted = cls._normalize_weight_map(rule.weighted_indicators)

        matched = [indicator for indicator in expected if indicator in observed]
        missing = [indicator for indicator in expected if indicator not in observed]
        missing_required = [indicator for indicator in required if indicator not in observed]
        critical_hits = [
            indicator for indicator in critical_failure_indicators
            if indicator in observed or indicator in artifact_risk_flags
        ]

        requires_human_review = bool(getattr(artifact, "requires_human_review", False))
        max_score = Decimal(str(rule.max_score))
        raw_score = cls._calculate_rule_score(
            rule=rule,
            matched=matched,
            expected=expected,
            weighted=weighted,
            max_score=max_score,
        )
        rubric = None
        spec_entry = rubric_specs.spec_for(rule_set.question_set_version, rule.question_code) if rule.question_code else None
        if spec_entry is not None:
            # SCR-01 to SCR-08: the question's own approved score note decides
            # the band. No Must Include point is mandatory by default (SCR-06),
            # so the old "any missing required point scores 0" rule and the
            # unapproved D-02 drafts in required_indicators no longer apply.
            spec_version, spec = spec_entry
            negative_ordinals = {
                position for position, indicator in enumerate(critical_failure_indicators, start=1)
                if indicator in observed or indicator in artifact_risk_flags
            } | cls._negative_evidence_ordinals(artifact, rule.question_code)
            rubric = rubric_specs.execute(
                spec,
                max_score=max_score,
                matched_ordinals=[position for position, indicator in enumerate(expected, start=1) if indicator in observed],
                total_points=len(expected),
                negative_ordinals=sorted(negative_ordinals),
            )
            rubric["spec_version"] = spec_version
            if rubric["status"] == rubric_specs.STATUS_PENDING:
                # Decision A (20 Oct 2026): no interim score. The answer stays
                # unscored until its rule is approved, then is recalculated.
                ResponseEvaluationResult.objects.filter(response=response, rule_set=rule_set).delete()
                if pending is not None:
                    pending.append(rule.question_code)
                return None
            raw_score = rubric["score"]
            missing_required = []
            critical_hits = [
                critical_failure_indicators[position - 1]
                for position in rubric.get("negative_ordinals", [])
                if rubric["critical_failure"] and position <= len(critical_failure_indicators)
            ]
            # No point is mandatory, so no mandatory condition can fail; a
            # partial answer is a scored band, not a review case.
            passed_required = True
        else:
            passed_required = not missing_required and bool(expected or matched or observed)
            # Pre-pooled versions (GOV1.2 and history) keep the scoring they
            # were released with: any missing required point scores 0.
            if missing_required:
                raw_score = cls.DECIMAL_ZERO

        critical_failure = bool(critical_hits)
        effective_score = raw_score
        if critical_failure:
            effective_score = cls.DECIMAL_ZERO

        percentage = cls._percentage(raw_score, max_score)
        template = getattr(response.question, "question_template", None)
        competency_code = (
            normalize_skill_code(getattr(artifact, "competency_code", ""))
            or normalize_skill_code(rule.competency_code)
            or normalize_skill_code(
                getattr(template, "skill_id", "")
                or getattr(template, "skill_tag", "")
                or getattr(template, "skill", "")
            )
        )
        competency_name = normalize_skill_tag(
            rule.competency_name or competency_code or getattr(template, "skill_tag", "")
        )
        explanation = cls._build_explanation(
            matched=matched,
            missing=missing,
            missing_required=missing_required,
            critical_hits=critical_hits,
            requires_human_review=requires_human_review,
            rubric=rubric,
        )

        result, _ =ResponseEvaluationResult.objects.update_or_create(
            response=response,
            rule_set=rule_set,
            defaults={
                "evaluation": evaluation,
                "session": evaluation.session,
                "candidate": evaluation.candidate,
                "question": response.question,
                "rule": rule,
                "competency_code": competency_code,
                "competency_name": competency_name,
                "score": raw_score,
                "max_score": max_score,
                "percentage": percentage,
                "passed_required_indicators": passed_required,
                "critical_failure": critical_failure,
                "requires_human_review": requires_human_review,
                "observed_indicators": observed,
                "matched_indicators": matched,
                "missing_indicators": missing,
                "risk_flags": sorted(set(artifact_risk_flags + rule_risk_flags)),
                "explanation": explanation,
                "metadata": {
                    "rule_id": str(rule.public_id),
                    "question_code": rule.question_code,
                    "scoring_method": rule.scoring_method,
                    "source_interpretation_status": getattr(artifact, "source_interpretation_status", ""),
                    "review_reason": getattr(artifact, "review_reason", ""),
                    "critical_failure_indicators": critical_failure_indicators,
                    "raw_score": str(raw_score),
                    "effective_score": str(effective_score),
                    **({
                        "rubric_spec_version": rubric["spec_version"],
                        "rubric_band": rubric["band"],
                        "rubric_status": rubric["status"],
                        "rubric_missing_count": rubric["missing_count"],
                    } if rubric else {}),
                },
                "scored_at": timezone.now(),
            },
        )
        return result

    @classmethod
    def _negative_evidence_ordinals(cls, artifact, question_code):
        """Negative indicators the interpreter confirmed as the candidate's
        own affirmed conduct (indicator-ID mode, SCR-07 context rules
        already applied), as 1-based positions in the question's list."""
        ordinals = set()
        prefix = f"V2-{question_code}-NI-"
        for indicator_id in (getattr(artifact, "metadata", None) or {}).get("negative_evidence_ids") or []:
            indicator_id = str(indicator_id)
            if indicator_id.startswith(prefix) and indicator_id[len(prefix):].isdigit():
                ordinals.add(int(indicator_id[len(prefix):]))
        return ordinals

    @classmethod
    def _resolve_rule(cls, *, rule_set, response, artifact):
        question_template = getattr(response.question, "question_template", None)
        if question_template:
            rule = rule_set.rules.filter(question_template=question_template, is_active=True).first()
            if rule:
                return rule
        question_code = getattr(question_template, "question_code", "")
        if question_code:
            rule = rule_set.rules.filter(question_code=question_code, is_active=True).first()
            if rule:
                return rule
        raw_competency_code = getattr(artifact, "competency_code", "")
        candidate_codes = []
        if raw_competency_code:
            candidate_codes.append(raw_competency_code)
        normalized_competency_code = normalize_skill_code(raw_competency_code)
        if normalized_competency_code and normalized_competency_code not in candidate_codes:
            candidate_codes.append(normalized_competency_code)
        for competency_code in candidate_codes:
            rule = rule_set.rules.filter(competency_code=competency_code, is_active=True).first()
            if rule:
                return rule
        return (
            rule_set.rules.filter(is_active=True)
            .filter(question_template__isnull=True, question_code="", competency_code="")
            .first()
        )

    @classmethod
    def _calculate_rule_score(cls, *, rule, matched, expected, weighted, max_score):
        if rule.scoring_method == ScoringRule.SCORING_METHOD_ALL_OR_NOTHING:
            return max_score if expected and len(matched) == len(expected) else cls.DECIMAL_ZERO

        # FIX-02: the governance importers once rounded each point's share
        # to a whole number, leaving the last point of a 6-point list at 0
        # and of a 7-point list at -1 (HK-TSK-002). A map with a zero or
        # negative weight is not a valid weighting, so such rules fall back
        # to equal weights, and no score can exceed the question maximum.
        if weighted and all(weight > 0 for weight in weighted.values()):
            matched_weight = sum(weighted.get(indicator, cls.DECIMAL_ZERO) for indicator in matched)
            total_weight = sum(weighted.values()) or cls.DECIMAL_ZERO
            if total_weight > 0:
                return min(max_score, cls._quantize(max_score * (matched_weight / total_weight)))

        if expected:
            return cls._quantize(max_score * (Decimal(len(matched)) / Decimal(len(expected))))
        return cls.DECIMAL_ZERO

    @classmethod
    def _aggregate_competencies(cls, *, evaluation, rule_set, responses, response_results):
        result_by_response = {result.response_id: result for result in response_results}
        grouped = {}
        for response in responses:
            artifact = getattr(response, "evaluation_input_artifact", None)
            result = result_by_response.get(response.id)
            competency_code = ""
            competency_name = ""
            if result is not None:
                competency_code = result.competency_code
                competency_name = result.competency_name
            elif artifact is not None:
                competency_code = normalize_skill_code(artifact.competency_code)
                competency_name = normalize_skill_tag(artifact.competency_code)
            grouped.setdefault(competency_code or "unmapped", {
                "name": competency_name or normalize_skill_tag(competency_code or "unmapped"),
                "responses": [],
            })["responses"].append((response, result))

        role_profile = cls._bank_role_profile(rule_set, evaluation)
        saved = []
        for competency_code, payload in grouped.items():
            items = payload["responses"]
            response_count = len(items)
            completed = [result for _, result in items if result is not None]
            incomplete_count = response_count - len(completed)
            total_score = sum((Decimal(str(result.score)) for result in completed), cls.DECIMAL_ZERO)
            max_score = sum((Decimal(str(result.max_score)) for result in completed), cls.DECIMAL_ZERO)
            percentage = cls._percentage(total_score, max_score)
            if role_profile is not None:
                # OUT-02 / management decision 5 (20 Oct 2026): one fixed pass
                # mark per role and competency - 60% unless the role profile
                # carries an approved exception - whichever questions were drawn.
                threshold_percentage = cls._quantize(
                    (role_profile.get("competency_thresholds") or {}).get(
                        competency_code, cls.DEFAULT_COMPETENCY_PASS_PERCENTAGE
                    )
                )
            else:
                # Pre-pooled versions keep the per-question thresholds they
                # were released with.
                threshold_total = sum(
                    (Decimal(str(result.rule.pass_threshold)) for result in completed if result.rule), cls.DECIMAL_ZERO
                )
                threshold_percentage = cls._percentage(threshold_total, max_score)
            # All rules mapped to a given competency_code are expected to
            # share one evaluation_layer (a competency belongs to one of
            # Cognitive/Behavioral/Task Execution) - take it from whichever
            # scored rule is available. Blank on rule sets that haven't
            # been categorized, same as the ScoringRule field itself.
            evaluation_layer = next(
                (result.rule.evaluation_layer for result in completed if result.rule and result.rule.evaluation_layer),
                "",
            )

            if response_count == 0:
                status = CompetencyEvaluationResult.STATUS_NOT_STARTED
            elif incomplete_count > 0:
                status = CompetencyEvaluationResult.STATUS_INCOMPLETE
            elif percentage < threshold_percentage:
                status = CompetencyEvaluationResult.STATUS_BELOW_THRESHOLD
            else:
                status = CompetencyEvaluationResult.STATUS_MEETS_THRESHOLD

            competency_result, _ = CompetencyEvaluationResult.objects.update_or_create(
                evaluation=evaluation,
                rule_set=rule_set,
                competency_code=competency_code,
                defaults={
                    "session": evaluation.session,
                    "candidate": evaluation.candidate,
                    "competency_name": payload["name"],
                    "evaluation_layer": evaluation_layer,
                    "total_score": total_score,
                    "max_score": max_score,
                    "percentage": percentage,
                    "pass_threshold": threshold_percentage,
                    "status": status,
                    "response_count": response_count,
                    "completed_response_count": len(completed),
                    "incomplete_response_count": incomplete_count,
                    "metadata": {
                        "response_ids": [str(response.public_id) for response, _ in items],
                    },
                },
            )
            saved.append(competency_result)

        # A re-score can change WHICH competency_code values a response
        # maps to (e.g. a question's skill_tag was retagged since the last
        # run) - update_or_create above only ever creates/updates the
        # codes seen THIS run, so a code from a prior run that's no longer
        # produced would otherwise survive as an orphaned, stale row (still
        # visible on reports/certificates generated from this evaluation).
        CompetencyEvaluationResult.objects.filter(evaluation=evaluation, rule_set=rule_set).exclude(
            competency_code__in=grouped.keys()
        ).delete()

        return saved

    @classmethod
    def _bank_role_profile(cls, rule_set, evaluation):
        """The approved role profile of a pooled bank version (GOV2.0+), or
        None for pre-pooled versions."""
        version = rule_set.question_set_version
        if not version:
            return None
        from api.interviews.models import QuestionBankVersion

        bank = QuestionBankVersion.objects.filter(version=version).first()
        if bank is None:
            return None
        return bank.profile_for(rule_set.role_code or evaluation.session.role_code) or {}

    @classmethod
    def _compute_layer_breakdown(cls, competency_results):
        """Buckets each scored competency by its evaluation_layer and
        returns (layer_breakdown, weighted_final_percentage). Competencies
        with no evaluation_layer set are excluded from both - if that
        leaves zero layers with any data (e.g. this rule set has never
        been categorized), weighted_final_percentage is None and the
        caller falls back to the flat score/max_score calculation instead
        of fabricating a Cognitive/Behavioral/Task breakdown that isn't real.
        """
        by_layer = {}
        for result in competency_results:
            layer = result.evaluation_layer
            if not layer:
                continue
            bucket = by_layer.setdefault(layer, {"total_score": cls.DECIMAL_ZERO, "max_score": cls.DECIMAL_ZERO})
            bucket["total_score"] += result.total_score
            bucket["max_score"] += result.max_score

        if not by_layer:
            return {}, None

        breakdown = {}
        present_weight_sum = Decimal("0")
        for layer, bucket in by_layer.items():
            percentage = cls._percentage(bucket["total_score"], bucket["max_score"])
            weight = EvaluationLayer.WEIGHTS.get(layer, 0)
            breakdown[layer] = {"percentage": float(percentage), "weight": weight}
            present_weight_sum += Decimal(str(weight))

        if present_weight_sum == 0:
            return breakdown, None

        # Layers missing from this rule set have their weight redistributed
        # proportionally among the ones present, rather than silently
        # scoring against a denominator that assumes all three exist.
        weighted_total = sum(
            (Decimal(str(data["percentage"])) * Decimal(str(data["weight"])) for data in breakdown.values()),
            cls.DECIMAL_ZERO,
        )
        final_percentage = cls._quantize(weighted_total / present_weight_sum)
        return breakdown, final_percentage

    @classmethod
    def _build_session_summary(cls, *, evaluation, rule_set, responses, response_results, competency_results,
                               unanswered_questions=(), rubric_pending=()):
        total_score = sum((Decimal(str(result.score)) for result in response_results), cls.DECIMAL_ZERO)
        max_score = sum((Decimal(str(result.max_score)) for result in response_results), cls.DECIMAL_ZERO)
        layer_breakdown, weighted_percentage = cls._compute_layer_breakdown(competency_results)
        overall_percentage = weighted_percentage if weighted_percentage is not None else cls._percentage(total_score, max_score)
        if rule_set.aggregation_method == ScoringRuleSet.AGGREGATION_EQUAL_COMPETENCY:
            # Each scored competency counts equally, however many questions
            # it had - question counts must not set competency weight.
            scored = [result for result in competency_results if result.completed_response_count]
            if scored:
                overall_percentage = cls._quantize(
                    sum((Decimal(str(result.percentage)) for result in scored), cls.DECIMAL_ZERO) / Decimal(len(scored))
                )
        incomplete_response_count = max(len(responses) - len(response_results), 0)
        critical_failures = [
            {
                "response_id": str(result.response.public_id),
                "question_id": str(result.question.public_id),
                "competency_code": result.competency_code,
                "question_code": result.metadata.get("question_code", ""),
                "topic": result.competency_name or result.question.skill,
                "score": float(result.score),
                "effective_score": float(result.metadata.get("effective_score", result.score)),
                "explanation": result.explanation,
            }
            for result in response_results
            if result.critical_failure
        ]
        below_threshold = [
            {
                "competency_code": result.competency_code,
                "competency_name": result.competency_name,
                "percentage": float(result.percentage),
                "pass_threshold": float(result.pass_threshold),
            }
            for result in competency_results
            if result.status == CompetencyEvaluationResult.STATUS_BELOW_THRESHOLD
        ]
        if incomplete_response_count > 0 or unanswered_questions or any(
            result.status == CompetencyEvaluationResult.STATUS_INCOMPLETE for result in competency_results
        ):
            status = SessionEvaluationSummary.STATUS_PARTIALLY_EVALUATED
        elif critical_failures:
            status = SessionEvaluationSummary.STATUS_EVALUATION_FAILED
        elif not response_results:
            status = SessionEvaluationSummary.STATUS_PENDING
        elif any(result.requires_human_review for result in response_results):
            # The evidence exists, but interpretation uncertainty means a
            # reliable outcome is unavailable. Do not release a guessed
            # score or turn this into a zero; represent it as incomplete.
            status = SessionEvaluationSummary.STATUS_PARTIALLY_EVALUATED
        else:
            status = SessionEvaluationSummary.STATUS_EVALUATED

        summary, _ = SessionEvaluationSummary.objects.update_or_create(
            evaluation=evaluation,
            rule_set=rule_set,
            defaults={
                "session": evaluation.session,
                "candidate": evaluation.candidate,
                "total_score": total_score,
                "max_score": max_score,
                "overall_percentage": overall_percentage,
                "layer_breakdown": layer_breakdown,
                "evaluated_response_count": len(response_results),
                "total_response_count": len(responses),
                "incomplete_response_count": incomplete_response_count,
                "competencies_summary": [
                    {
                        "competency_code": result.competency_code,
                        "competency_name": result.competency_name,
                        "percentage": float(result.percentage),
                        "status": result.status,
                        "response_count": result.response_count,
                        "completed_response_count": result.completed_response_count,
                    }
                    for result in competency_results
                ],
                "critical_failures": critical_failures,
                "below_threshold_competencies": below_threshold,
                "status": status,
                "metadata": {
                    "rule_set_name": rule_set.name,
                    "rule_set_version": rule_set.version,
                    "session_role_code": evaluation.session.role_code,
                    "assigned_question_count": len(responses) + len(unanswered_questions),
                    "unanswered_question_count": len(unanswered_questions),
                    "unanswered_question_orders": [question.question_order for question in unanswered_questions],
                    # Answers whose band awaits an approved scoring rule
                    # (decision A, 20 Oct 2026); recalculated on approval.
                    "rubric_pending_question_codes": list(rubric_pending),
                },
                "generated_at": timezone.now(),
            },
        )
        return summary

    @classmethod
    def _apply_evaluation_rollups(cls, *, evaluation, summary, actor=None):
        session = getattr(evaluation, "session", None)
        if session is not None and session.is_scheduled_interview:
            # SCH-07 / GOV-02: a scheduled assessment's outcome comes from the
            # evaluator's ratings. The AI summary of its transcript is kept as
            # reference only and never writes the evaluation's score,
            # readiness or readiness record, before or after the rating.
            return
        existing_record = EvaluationReadinessRecordService.get_existing(evaluation)
        if existing_record is not None:
            evaluation.readiness_status = EvaluationReadinessRecordService.status_from_indicator(
                existing_record.readiness_indicator
            )
            evaluation.readiness_override_applied = existing_record.override_triggered
            evaluation.readiness_override_reason = existing_record.readiness_reason
            evaluation.score = summary.overall_percentage
            evaluation.save(
                update_fields=[
                    "score",
                    "readiness_status",
                    "readiness_override_applied",
                    "readiness_override_reason",
                    "updated_at",
                ]
            )
            return

        update_fields = ["score", "updated_at"]
        evaluation.score = summary.overall_percentage
        readiness_reason = ""
        override_triggered = False
        record_metadata = {
            "summary_status": summary.status,
            "overall_percentage": str(summary.overall_percentage),
            "rule_set_version": summary.rule_set.version,
        }

        if not evaluation.readiness_indicator_enabled:
            evaluation.readiness_status = ReadinessStatus.PENDING
            evaluation.readiness_override_applied = False
            evaluation.readiness_override_reason = ""
        elif summary.critical_failures:
            # OUT-03: a confirmed critical failure prevents Ready.
            first_failure = summary.critical_failures[0]
            topic = first_failure.get("topic")
            evaluation.readiness_status = ReadinessStatus.NOT_READY
            evaluation.readiness_override_applied = True
            readiness_reason = f"Failed critical question: {first_failure.get('question_code') or 'UNKNOWN'}"
            if topic:
                readiness_reason = f"{readiness_reason} ({topic})"
            override_triggered = True
            record_metadata["critical_failure"] = first_failure
            evaluation.readiness_override_reason = readiness_reason
            update_fields.extend(["readiness_status", "readiness_override_applied", "readiness_override_reason"])
        elif summary.status == SessionEvaluationSummary.STATUS_EVALUATED:
            # Scoring completing cleanly (no critical failures) is necessary
            # but not sufficient for READY - the assessment must also have
            # actually covered enough distinct competencies. Every response
            # can be answered and scored (summary.status == STATUS_EVALUATED
            # only checks that) while the assigned question set itself never
            # touches enough of this role's required canonical dimensions -
            # reusing the exact same role-aware check certificate_eligibility()
            # already applies so a READY evaluation and an eligible-for-
            # certificate evaluation never disagree about whether coverage
            # was sufficient.
            role_code = evaluation.session.role_code
            role_competency_result = evaluate_role_competency_readiness(summary, role_code)
            if role_competency_result is not None:
                # Migrated role (currently just Driver-v1.2): Required/
                # Critical model decides INCOMPLETE / NOT_READY /
                # PARTIALLY_READY / READY directly - see
                # certificate_services.evaluate_role_competency_readiness
                # for the exact rules.
                status, readiness_reason = role_competency_result
                evaluation.readiness_status = status
                if status == ReadinessStatus.READY:
                    evaluation.readiness_override_applied = False
                    evaluation.readiness_override_reason = ""
                elif status == ReadinessStatus.INCOMPLETE:
                    evaluation.readiness_override_applied = False
                    evaluation.readiness_override_reason = ""
                else:
                    # NOT_READY or PARTIALLY_READY - the indicator overrides
                    # the raw score-based narrative either way.
                    evaluation.readiness_override_applied = True
                    override_triggered = True
                    evaluation.readiness_override_reason = readiness_reason
                    record_metadata["role_competency_status"] = status
            else:
                covered_dimensions = assessed_dimensions(summary)
                role_required_dimensions = required_dimensions_for_role(role_code)
                minimum_dimensions = minimum_required_dimensions_for_role(role_code)
                # A dimension can be "covered" (assessed_dimensions above) yet
                # still be a real fail: below_threshold_competencies is computed
                # by _build_session_summary but was previously never consulted
                # here, so a required competency that was fully assessed and
                # scored below its own pass_threshold still rolled up to READY.
                failed_required_dimensions = below_threshold_dimensions(summary) & set(role_required_dimensions)
                if len(covered_dimensions) < minimum_dimensions:
                    evaluation.readiness_status = ReadinessStatus.INCOMPLETE
                    evaluation.readiness_override_applied = False
                    evaluation.readiness_override_reason = ""
                    readiness_reason = (
                        f"Insufficient assessment coverage: {len(covered_dimensions)} of "
                        f"{len(role_required_dimensions)} competencies assessed, minimum required is "
                        f"{minimum_dimensions}."
                    )
                elif failed_required_dimensions:
                    evaluation.readiness_status = ReadinessStatus.NOT_READY
                    evaluation.readiness_override_applied = True
                    override_triggered = True
                    failed_names = ", ".join(sorted(failed_required_dimensions))
                    readiness_reason = f"Required competency below threshold: {failed_names}."
                    record_metadata["below_threshold_dimensions"] = sorted(failed_required_dimensions)
                    evaluation.readiness_override_reason = readiness_reason
                else:
                    evaluation.readiness_status = ReadinessStatus.READY
                    evaluation.readiness_override_applied = False
                    evaluation.readiness_override_reason = ""
                    readiness_reason = "Deterministic scoring completed without critical failures."
            update_fields.extend(["readiness_status", "readiness_override_applied", "readiness_override_reason"])
        elif summary.status == SessionEvaluationSummary.STATUS_PARTIALLY_EVALUATED:
            evaluation.readiness_status = ReadinessStatus.INCOMPLETE
            evaluation.readiness_override_applied = False
            evaluation.readiness_override_reason = ""
            readiness_reason = "Insufficient evidence to determine a complete assessment result."
            pending_codes = (summary.metadata or {}).get("rubric_pending_question_codes") or []
            if pending_codes:
                # OUT-05: show the actual cause, not poor performance.
                readiness_reason = (
                    f"Scoring rule awaiting approval for {len(pending_codes)} answer(s) "
                    f"({', '.join(pending_codes)}); the result will be recalculated once it is approved."
                )
                evaluation.readiness_override_reason = readiness_reason
            update_fields.extend(["readiness_status", "readiness_override_applied", "readiness_override_reason"])
        else:
            evaluation.readiness_status = ReadinessStatus.PENDING
            evaluation.readiness_override_applied = False
            evaluation.readiness_override_reason = ""
            readiness_reason = (
                "Evaluation remains in a medium state because scoring is incomplete, pending review, "
                "or requires human follow-up."
            )
            update_fields.extend(["readiness_status", "readiness_override_applied", "readiness_override_reason"])

        evaluation.save(update_fields=list(dict.fromkeys(update_fields)))
        if evaluation.readiness_indicator_enabled and summary.status != SessionEvaluationSummary.STATUS_PARTIALLY_EVALUATED:
            EvaluationReadinessRecordService.persist_once(
                evaluation=evaluation,
                readiness_status=evaluation.readiness_status,
                readiness_reason=readiness_reason,
                override_triggered=override_triggered,
                actor=actor,
                metadata=record_metadata,
            )

    @classmethod
    def _log_scoring_events(cls, *, actor, evaluation, rule_set, response_results, competency_results, summary):
        if actor is None:
            return
        AuditLogService.log(
            user=actor,
            action=AuditLogAction.SCORE_SET_RECALCULATED,
            category=AuditLogCategory.EVALUATION,
            description=f"Week 6 deterministic scoring completed for evaluation {evaluation.public_id}",
            resource=evaluation,
            data={
                "rule_set_version": rule_set.version,
                "response_result_count": len(response_results),
                "competency_result_count": len(competency_results),
                "summary_status": summary.status,
                "overall_percentage": float(summary.overall_percentage),
            },
        )

    @classmethod
    def _normalize_indicator_list(cls, items):
        if not isinstance(items, list):
            return []
        normalized = []
        for item in items:
            if isinstance(item, dict):
                value = item.get("code") or item.get("indicator") or item.get("name") or item.get("value")
            else:
                value = item
            if value in (None, ""):
                continue
            normalized.append(str(value).strip().lower())
        return normalized

    @classmethod
    def _normalize_weight_map(cls, value):
        if not isinstance(value, dict):
            return {}
        return {
            str(key).strip().lower(): Decimal(str(weight))
            for key, weight in value.items()
            if key not in (None, "") and weight is not None
        }

    @classmethod
    def _build_explanation(cls, *, matched, missing, missing_required, critical_hits, requires_human_review,
                           rubric=None):
        parts = []
        if rubric:
            parts.append(f"Rubric band: {rubric['band']} ({rubric['score']}).")
        if matched:
            parts.append(f"Matched indicators: {', '.join(matched)}.")
        if missing:
            parts.append(f"Missing indicators: {', '.join(missing)}.")
        if missing_required:
            parts.append(f"Required indicators missing: {', '.join(missing_required)}.")
        if critical_hits:
            parts.append(f"Critical failure indicators detected: {', '.join(critical_hits)}.")
        if requires_human_review:
            parts.append("Human review is still required for this response.")
        if not parts:
            return "No scoring evidence was available."
        return " ".join(parts)

    @classmethod
    def _percentage(cls, value, max_value):
        value = Decimal(str(value or 0))
        max_value = Decimal(str(max_value or 0))
        if max_value <= 0:
            return cls.DECIMAL_ZERO
        return cls._quantize((value / max_value) * cls.DECIMAL_HUNDRED)

    @classmethod
    def _quantize(cls, value):
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
