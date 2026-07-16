from django.utils import timezone

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, ReadinessStatus

from .models import EvaluationReadinessDecisionRecord


class EvaluationReadinessRecordService:
    RULE_ENGINE_VERSION = "v1.0"

    INDICATOR_READY = "جاهز"
    INDICATOR_MEDIUM = "متوسط"
    INDICATOR_NOT_READY = "غير جاهز"

    @classmethod
    def persist_once(
        cls,
        *,
        evaluation,
        readiness_status,
        readiness_reason="",
        override_triggered=False,
        actor=None,
        metadata=None,
    ):
        if not getattr(evaluation, "session", None):
            return None

        try:
            existing = evaluation.readiness_legal_record
        except EvaluationReadinessDecisionRecord.DoesNotExist:
            existing = None
        if existing is not None:
            return existing

        record = EvaluationReadinessDecisionRecord.objects.create(
            evaluation=evaluation,
            session=evaluation.session,
            readiness_indicator=cls._map_indicator(readiness_status),
            readiness_reason=readiness_reason,
            override_triggered=override_triggered,
            rule_engine_version=cls.RULE_ENGINE_VERSION,
            decided_at=timezone.now(),
            metadata=metadata or {},
        )

        payload = {
            "session_id": str(evaluation.session.public_id),
            "evaluation_id": str(evaluation.public_id),
            "readiness_indicator": record.readiness_indicator,
            "readiness_reason": record.readiness_reason,
            "override_triggered": record.override_triggered,
            "rule_engine_version": record.rule_engine_version,
            "decided_at": record.decided_at.isoformat(),
            "immutable_record_id": str(record.public_id),
            "metadata": record.metadata,
        }
        description = (
            f"Immutable readiness legal record stored for evaluation {evaluation.public_id}"
        )
        if actor:
            AuditLogService.log(
                user=actor,
                action=AuditLogAction.RULE_ENGINE_DECISION_RECORDED,
                category=AuditLogCategory.EVALUATION,
                description=description,
                resource=evaluation,
                data=payload,
            )
        else:
            AuditLogService.log_system(
                action=AuditLogAction.RULE_ENGINE_DECISION_RECORDED,
                category=AuditLogCategory.EVALUATION,
                description=description,
                resource=evaluation,
                data=payload,
            )
        return record

    @classmethod
    def get_existing(cls, evaluation):
        try:
            return evaluation.readiness_legal_record
        except EvaluationReadinessDecisionRecord.DoesNotExist:
            return None

    @classmethod
    def status_from_indicator(cls, readiness_indicator):
        if readiness_indicator == cls.INDICATOR_READY:
            return ReadinessStatus.READY
        if readiness_indicator == cls.INDICATOR_NOT_READY:
            return ReadinessStatus.NOT_READY
        return ReadinessStatus.PENDING

    @classmethod
    def _map_indicator(cls, readiness_status):
        if readiness_status == ReadinessStatus.READY:
            return cls.INDICATOR_READY
        if readiness_status == ReadinessStatus.NOT_READY:
            return cls.INDICATOR_NOT_READY
        return cls.INDICATOR_MEDIUM
