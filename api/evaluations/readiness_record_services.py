from django.utils import timezone

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, ReadinessStatus

from .models import EvaluationReadinessCorrection, EvaluationReadinessDecisionRecord


class EvaluationReadinessRecordService:
    RULE_ENGINE_VERSION = "v1.0"

    INDICATOR_READY = "جاهز"
    INDICATOR_MEDIUM = "متوسط"
    INDICATOR_NOT_READY = "غير جاهز"
    INDICATOR_INCOMPLETE = "أدلة غير كافية"

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
    def get_correction(cls, evaluation):
        try:
            return evaluation.readiness_correction
        except (EvaluationReadinessCorrection.DoesNotExist, AttributeError):
            # AttributeError covers lightweight non-model stand-ins used in
            # some existing tests (e.g. SimpleNamespace) that don't carry
            # Django's reverse-relation descriptors - never a real
            # evaluation lacking this, since every Evaluation instance has
            # the descriptor even with no row behind it.
            return None

    @classmethod
    def current_readiness_status(cls, evaluation):
        """The evaluation's readiness status as it actually stands today:
        the corrected value from an EvaluationReadinessCorrection when one
        exists, otherwise evaluation.readiness_status unchanged. The
        original EvaluationReadinessDecisionRecord is never consulted or
        altered here - it's left exactly as decided, for history; this is
        the "what's true now" read, for anything (report, dashboard, a
        future certificate flow) that needs the authoritative current
        answer rather than the frozen original one."""
        correction = cls.get_correction(evaluation)
        if correction is not None:
            return correction.corrected_readiness_status
        return evaluation.readiness_status

    @classmethod
    def apply_correction(cls, *, evaluation, corrected_status, reason, actor=None, metadata=None):
        """Records a formal, auditable correction to an evaluation's
        readiness result without touching the original immutable record or
        evaluation.readiness_status itself - both remain exactly as they
        were for history. Idempotent: calling this again for an evaluation
        that already has a correction returns the existing one unchanged
        (immutable once created, same as EvaluationReadinessDecisionRecord -
        a further change would need a new, distinct correction mechanism,
        not an edit to this one)."""
        existing = cls.get_correction(evaluation)
        if existing is not None:
            return existing

        correction = EvaluationReadinessCorrection.objects.create(
            evaluation=evaluation,
            original_readiness_status=evaluation.readiness_status,
            corrected_readiness_status=corrected_status,
            reason=reason,
            corrected_by=actor,
            metadata=metadata or {},
        )
        description = (
            f"Readiness correction recorded for evaluation {evaluation.public_id}: "
            f"{correction.original_readiness_status} -> {correction.corrected_readiness_status}. "
            f"Reason: {reason}"
        )
        log_data = {
            "evaluation_id": str(evaluation.public_id),
            "original_readiness_status": correction.original_readiness_status,
            "corrected_readiness_status": correction.corrected_readiness_status,
            "reason": reason,
        }
        if actor:
            AuditLogService.log(
                user=actor,
                action=AuditLogAction.EVALUATION_READINESS_CORRECTED,
                category=AuditLogCategory.EVALUATION,
                description=description,
                resource=evaluation,
                data=log_data,
            )
        else:
            AuditLogService.log_system(
                action=AuditLogAction.EVALUATION_READINESS_CORRECTED,
                category=AuditLogCategory.EVALUATION,
                description=description,
                resource=evaluation,
                data=log_data,
            )
        return correction

    @classmethod
    def status_from_indicator(cls, readiness_indicator):
        if readiness_indicator == cls.INDICATOR_READY:
            return ReadinessStatus.READY
        if readiness_indicator == cls.INDICATOR_NOT_READY:
            return ReadinessStatus.NOT_READY
        if readiness_indicator == cls.INDICATOR_INCOMPLETE:
            return ReadinessStatus.INCOMPLETE
        if readiness_indicator == cls.INDICATOR_MEDIUM:
            # INDICATOR_MEDIUM is also the persisted fallback for a locked
            # PENDING record (a rare case: readiness_indicator_enabled=True
            # but summary.status was PARTIALLY_EVALUATED/
            # REQUIRES_HUMAN_REVIEW when the record was written - see
            # _map_indicator's own fallback below). EvaluationReportService.
            # _resolve_readiness_indicator already displays that same record
            # as "Partially Ready", not "Pending", so resolving it back to
            # PARTIALLY_READY here keeps the internal status consistent with
            # what's already shown, rather than introducing a second
            # disagreement on top of an existing display ambiguity.
            return ReadinessStatus.PARTIALLY_READY
        return ReadinessStatus.PENDING

    @classmethod
    def _map_indicator(cls, readiness_status):
        if readiness_status == ReadinessStatus.READY:
            return cls.INDICATOR_READY
        if readiness_status == ReadinessStatus.NOT_READY:
            return cls.INDICATOR_NOT_READY
        if readiness_status == ReadinessStatus.INCOMPLETE:
            return cls.INDICATOR_INCOMPLETE
        if readiness_status == ReadinessStatus.PARTIALLY_READY:
            return cls.INDICATOR_MEDIUM
        return cls.INDICATOR_MEDIUM
