from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, ReadinessStatus
from api.sessions.models import CandidateResponse
from api.translation.models import EvaluationInputArtifact

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
    DECIMAL_ZERO = Decimal("0.00")
    DECIMAL_HUNDRED = Decimal("100.00")

    @classmethod
    @transaction.atomic
    def run_for_evaluation(cls, *, evaluation, actor=None, rule_set=None):
        session = getattr(evaluation, "session", None)
        if session is None:
            raise Week6ScoringError("Evaluation must be linked to an interview session before scoring")

        selected_rule_set = rule_set or cls._resolve_rule_set(evaluation)
        responses = list(
            CandidateResponse.objects.select_related(
                "question",
                "question__question_template",
                "evaluation_input_artifact",
            )
            .filter(session=session)
            .order_by("question__question_order", "created_at")
        )
        if not responses:
            raise Week6ScoringError("No candidate responses are available for scoring")

        results = []
        for response in responses:
            result = cls._score_response(
                evaluation=evaluation,
                response=response,
                rule_set=selected_rule_set,
            )
            if result is not None:
                results.append(result)
        competency_results = cls._aggregate_competencies(
            evaluation=evaluation,
            rule_set=selected_rule_set,
            responses=responses,
            response_results=results,
        )
        summary = cls._build_session_summary(
            evaluation=evaluation,
            rule_set=selected_rule_set,
            responses=responses,
            response_results=results,
            competency_results=competency_results,
        )
        cls._apply_evaluation_rollups(evaluation=evaluation, summary=summary, actor=actor)
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
    def _resolve_rule_set(cls, evaluation):
        session = evaluation.session
        queryset = ScoringRuleSet.objects.filter(
            evaluation_tier=evaluation.evaluation_tier,
            is_active=True,
        ).order_by("-created_at")
        if evaluation.company_id:
            company_queryset = queryset.filter(company=evaluation.company)
            if session.role_code:
                exact = company_queryset.filter(role_code=session.role_code).first()
                if exact:
                    return exact
            fallback = company_queryset.filter(role_code="").first()
            if fallback:
                return fallback
        if session.role_code:
            exact = queryset.filter(role_code=session.role_code).first()
            if exact:
                return exact
        fallback = queryset.filter(role_code="").first()
        if fallback:
            return fallback
        raise Week6ScoringError("No active scoring rule set matches this evaluation")

    @classmethod
    def _score_response(cls, *, evaluation, response, rule_set):
        artifact = getattr(response, "evaluation_input_artifact", None)
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

        requires_human_review = bool(getattr(artifact, "requires_human_review", False) or artifact is None)
        max_score = Decimal(str(rule.max_score))
        raw_score = cls._calculate_rule_score(
            rule=rule,
            matched=matched,
            expected=expected,
            weighted=weighted,
            max_score=max_score,
        )
        passed_required = not missing_required and bool(expected or matched or observed)
        if missing_required:
            raw_score = cls.DECIMAL_ZERO

        critical_failure = bool(critical_hits)
        effective_score = raw_score
        if critical_failure:
            effective_score = cls.DECIMAL_ZERO

        if requires_human_review and artifact is None:
            passed_required = False

        percentage = cls._percentage(raw_score, max_score)
        competency_code = (
            getattr(artifact, "competency_code", "")
            or rule.competency_code
            or getattr(response.question.question_template, "skill_tag", "")
        )
        competency_name = rule.competency_name or competency_code.replace("_", " ").title()
        explanation = cls._build_explanation(
            matched=matched,
            missing=missing,
            missing_required=missing_required,
            critical_hits=critical_hits,
            requires_human_review=requires_human_review,
        )

        result, _ = ResponseEvaluationResult.objects.update_or_create(
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
                },
                "scored_at": timezone.now(),
            },
        )
        return result

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
        competency_code = getattr(artifact, "competency_code", "")
        if competency_code:
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

        if weighted:
            matched_weight = sum(weighted.get(indicator, cls.DECIMAL_ZERO) for indicator in matched)
            total_weight = sum(weighted.values()) or cls.DECIMAL_ZERO
            if total_weight > 0:
                return cls._quantize(max_score * (matched_weight / total_weight))

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
                competency_code = artifact.competency_code
                competency_name = artifact.competency_code.replace("_", " ").title()
            grouped.setdefault(competency_code or "unmapped", {
                "name": competency_name or (competency_code or "unmapped").replace("_", " ").title(),
                "responses": [],
            })["responses"].append((response, result))

        saved = []
        for competency_code, payload in grouped.items():
            items = payload["responses"]
            response_count = len(items)
            completed = [result for _, result in items if result is not None]
            incomplete_count = response_count - len(completed)
            total_score = sum((Decimal(str(result.score)) for result in completed), cls.DECIMAL_ZERO)
            max_score = sum((Decimal(str(result.max_score)) for result in completed), cls.DECIMAL_ZERO)
            threshold_total = sum((Decimal(str(result.rule.pass_threshold)) for result in completed if result.rule), cls.DECIMAL_ZERO)
            percentage = cls._percentage(total_score, max_score)
            threshold_percentage = cls._percentage(threshold_total, max_score)

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
        return saved

    @classmethod
    def _build_session_summary(cls, *, evaluation, rule_set, responses, response_results, competency_results):
        total_score = sum((Decimal(str(result.score)) for result in response_results), cls.DECIMAL_ZERO)
        max_score = sum((Decimal(str(result.max_score)) for result in response_results), cls.DECIMAL_ZERO)
        overall_percentage = cls._percentage(total_score, max_score)
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
        requires_human_review = any(result.requires_human_review for result in response_results)

        if not response_results:
            status = SessionEvaluationSummary.STATUS_PENDING
        elif requires_human_review:
            status = SessionEvaluationSummary.STATUS_REQUIRES_HUMAN_REVIEW
        elif critical_failures:
            status = SessionEvaluationSummary.STATUS_EVALUATION_FAILED
        elif incomplete_response_count > 0 or any(
            result.status == CompetencyEvaluationResult.STATUS_INCOMPLETE for result in competency_results
        ):
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
                },
                "generated_at": timezone.now(),
            },
        )
        return summary

    @classmethod
    def _apply_evaluation_rollups(cls, *, evaluation, summary, actor=None):
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
            evaluation.readiness_status = ReadinessStatus.READY
            evaluation.readiness_override_applied = False
            evaluation.readiness_override_reason = ""
            readiness_reason = "Deterministic scoring completed without critical failures."
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
        if evaluation.readiness_indicator_enabled:
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
    def _build_explanation(cls, *, matched, missing, missing_required, critical_hits, requires_human_review):
        parts = []
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
