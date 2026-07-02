from decimal import Decimal, InvalidOperation

from api.core.constants import InterviewEvaluationTier, ReadinessStatus


class EvaluationRuleEngine:
    SCORE_KEYS = ("score", "question_score", "normalized_score", "ai_score", "final_score")

    @classmethod
    def apply_readiness_rules(cls, evaluation):
        if (
            evaluation.evaluation_tier != InterviewEvaluationTier.FULL
            or not evaluation.readiness_indicator_enabled
        ):
            if evaluation.readiness_override_applied or evaluation.readiness_override_reason:
                evaluation.readiness_override_applied = False
                evaluation.readiness_override_reason = ""
            if evaluation.readiness_status != ReadinessStatus.PENDING:
                evaluation.readiness_status = ReadinessStatus.PENDING
            evaluation.save(
                update_fields=[
                    "readiness_status",
                    "readiness_override_applied",
                    "readiness_override_reason",
                    "updated_at",
                ]
            )
            return {
                "override_applied": False,
                "reason": None,
                "question_code": None,
            }

        result = cls._find_critical_zero_score(evaluation)
        if result is None:
            if evaluation.readiness_override_applied and evaluation.readiness_override_reason:
                evaluation.readiness_override_applied = False
                evaluation.readiness_override_reason = ""
                if evaluation.readiness_status == ReadinessStatus.NOT_READY:
                    evaluation.readiness_status = ReadinessStatus.PENDING
                evaluation.save(
                    update_fields=[
                        "readiness_status",
                        "readiness_override_applied",
                        "readiness_override_reason",
                        "updated_at",
                    ]
                )
            return {
                "override_applied": False,
                "reason": None,
                "question_code": None,
            }

        question_code, score = result
        evaluation.readiness_status = ReadinessStatus.NOT_READY
        evaluation.readiness_override_applied = True
        evaluation.readiness_override_reason = (
            f"Critical question {question_code or 'UNKNOWN'} scored 0. "
            "Readiness overridden by Rule Engine."
        )
        evaluation.save(
            update_fields=[
                "readiness_status",
                "readiness_override_applied",
                "readiness_override_reason",
                "updated_at",
            ]
        )
        return {
            "override_applied": True,
            "reason": evaluation.readiness_override_reason,
            "question_code": question_code,
            "score": score,
        }

    @classmethod
    def _find_critical_zero_score(cls, evaluation):
        session = getattr(evaluation, "session", None)
        if not session:
            return None

        responses = session.responses.select_related("question__question_template").all()
        for response in responses:
            template = getattr(response.question, "question_template", None)
            if not template or not template.critical_question:
                continue
            score = cls._extract_score(response.metadata)
            if score is not None and score == Decimal("0"):
                return template.question_code or str(template.public_id), score
        return None

    @classmethod
    def _extract_score(cls, metadata):
        if not isinstance(metadata, dict):
            return None
        for key in cls.SCORE_KEYS:
            if key not in metadata:
                continue
            try:
                return Decimal(str(metadata[key]))
            except (InvalidOperation, TypeError, ValueError):
                return None
        return None
