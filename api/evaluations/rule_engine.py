from decimal import Decimal, InvalidOperation

from api.core.constants import InterviewEvaluationTier, ReadinessStatus
from api.questions.skill_tags import normalize_skill_tag
from .readiness_record_services import EvaluationReadinessRecordService


class EvaluationRuleEngine:
    SCORE_KEYS = ("score", "question_score", "normalized_score", "ai_score", "final_score")

    @classmethod
    def apply_readiness_rules(cls, evaluation):
        existing_record = EvaluationReadinessRecordService.get_existing(evaluation)
        if existing_record is not None:
            cls._sync_evaluation_from_legal_record(evaluation, existing_record)
            return {
                "override_applied": existing_record.override_triggered,
                "reason": existing_record.readiness_reason,
                "question_code": existing_record.metadata.get("question_code", ""),
                "topic": existing_record.metadata.get("topic", ""),
                "frozen_legal_record": True,
            }

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

        question_code, topic, score = result
        evaluation.readiness_status = ReadinessStatus.NOT_READY
        evaluation.readiness_override_applied = True
        evaluation.readiness_override_reason = (
            f"Failed critical question: {question_code or 'UNKNOWN'}"
            f"{f' ({topic})' if topic else ''}"
        )
        evaluation.save(
            update_fields=[
                "readiness_status",
                "readiness_override_applied",
                "readiness_override_reason",
                "updated_at",
            ]
        )
        EvaluationReadinessRecordService.persist_once(
            evaluation=evaluation,
            readiness_status=evaluation.readiness_status,
            readiness_reason=evaluation.readiness_override_reason,
            override_triggered=True,
            metadata={
                "question_code": question_code or "",
                "topic": topic or "",
                "legacy_rule_engine": True,
                "score": str(score),
            },
        )
        return {
            "override_applied": True,
            "reason": evaluation.readiness_override_reason,
            "question_code": question_code,
            "topic": topic,
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
                topic = normalize_skill_tag(template.skill_tag or template.skill) or template.domain
                return template.question_code or str(template.public_id), topic, score
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

    @classmethod
    def _sync_evaluation_from_legal_record(cls, evaluation, record):
        evaluation.readiness_status = EvaluationReadinessRecordService.status_from_indicator(
            record.readiness_indicator
        )
        evaluation.readiness_override_applied = record.override_triggered
        evaluation.readiness_override_reason = record.readiness_reason
        evaluation.save(
            update_fields=[
                "readiness_status",
                "readiness_override_applied",
                "readiness_override_reason",
                "updated_at",
            ]
        )
