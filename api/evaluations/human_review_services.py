"""Audited human review for exceptional cases and individually held results.

Routine scoring and quality flags do not delay release. Existing held results
remain held until a reviewer records an audited decision; certificate
eligibility continues to apply its independent evidence and safety checks.
"""
from django.db import transaction
from django.utils import timezone

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, ReadinessStatus, Roles

REVIEWER_ROLES = (Roles.ADMIN, Roles.SUPERADMIN)
REVIEW_DECISIONS = (
    ReadinessStatus.READY,
    ReadinessStatus.PARTIALLY_READY,
    ReadinessStatus.NOT_READY,
    ReadinessStatus.INCOMPLETE,
)


class HumanReviewError(Exception):
    pass


def can_see_held_results(user):
    return bool(user and getattr(user, "is_authenticated", False) and getattr(user, "role", None) in REVIEWER_ROLES)


def is_hidden_from(evaluation, user):
    return bool(evaluation is not None and evaluation.is_held_for_review and not can_see_held_results(user))


class HumanReviewService:
    @classmethod
    def review_reasons(cls, evaluation, summary):
        # A valid scored outcome (including Not Ready and safety failures) is
        # released under the approved scoring rules. Unreliable/missing
        # evidence is represented as incomplete by the scoring service.
        # Exceptional incidents must be held explicitly by their incident
        # workflow; these routine scoring flags do not create a hold.
        return []

    @classmethod
    def _critical_question_reasons(cls, evaluation):
        """Legacy explanation helper retained for review history tooling.

        A critical failure is scored and disclosed under its approved rule;
        it does not hold the overall result."""
        from api.interviews.models import QuestionBankVersion

        session = getattr(evaluation, "session", None)
        version = getattr(session, "question_set_version", "")
        if not version or not QuestionBankVersion.objects.filter(version=version).exists():
            return []
        if evaluation.readiness_status not in (ReadinessStatus.READY, ReadinessStatus.PARTIALLY_READY):
            return []
        failed = sorted(
            result.question.question_template.question_code
            for result in evaluation.response_results.select_related("question__question_template")
            if result.question.question_template_id
            and result.question.question_template.critical_question
            and result.score == 0
        )
        if not failed:
            return []
        return [f"Critical question scored 0 ({', '.join(failed)}); not released on the overall result alone."]

    @classmethod
    def flag_after_scoring(cls, evaluation, summary):
        """Routine QA never delays result release; existing holds are intact."""
        return evaluation

    # Kept for compatibility with callers that inspect policy categories.
    HOLDING_REPORT_FLAGS = {"critical_failure", "low_confidence_interpretation", "transcript_issue", "translation_issue"}

    @classmethod
    def flag_from_report(cls, evaluation, report):
        """Report findings remain visible with the report; only an explicit
        exceptional-delivery incident workflow may block delivery."""
        return evaluation

    @classmethod
    def _hold(cls, evaluation, reasons):
        from api.evaluations.models import Evaluation

        merged = list(dict.fromkeys(list(evaluation.review_reasons or []) + list(reasons)))
        newly_held = evaluation.review_status != Evaluation.REVIEW_REQUIRED
        evaluation.review_status = Evaluation.REVIEW_REQUIRED
        evaluation.review_reasons = merged
        evaluation.save(update_fields=["review_status", "review_reasons", "updated_at"])
        if newly_held:
            AuditLogService.log_system(
                action=AuditLogAction.AI_PROCESSING_REQUIRES_HUMAN_REVIEW,
                category=AuditLogCategory.EVALUATION,
                description=f"Evaluation {evaluation.public_id} held for human review before release.",
                resource=evaluation,
                data={"evaluation_id": str(evaluation.public_id), "reasons": merged},
            )

    @classmethod
    @transaction.atomic
    def approve(cls, *, evaluation, reviewer, decision, notes):
        """Record the reviewer's readiness decision and release the result.
        The reviewer cannot bypass the methodology: a certificate is still
        only issued if the normal eligibility rules (Full Assessment,
        thresholds, identity, consent...) pass on the actual scores."""
        from api.evaluations.certificate_services import generate_certificate
        from api.evaluations.models import Evaluation
        from api.evaluations.readiness_record_services import EvaluationReadinessRecordService
        from api.reports.services import EvaluationReportError, EvaluationReportService

        if not can_see_held_results(reviewer):
            raise HumanReviewError("Only an admin reviewer can approve a held result.")
        if evaluation.session_id and evaluation.session.is_scheduled_interview:
            raise HumanReviewError(
                "Scheduled evaluator-led readiness is derived from ratings. "
                "Use the authorized evaluator rating correction workflow."
            )
        if evaluation.review_status != Evaluation.REVIEW_REQUIRED:
            raise HumanReviewError("This evaluation is not awaiting human review.")
        if decision not in REVIEW_DECISIONS:
            raise HumanReviewError(f"Decision must be one of: {', '.join(REVIEW_DECISIONS)}.")
        if not (notes or "").strip():
            raise HumanReviewError("Review notes are required.")

        EvaluationReadinessRecordService.apply_correction(
            evaluation=evaluation,
            corrected_status=decision,
            reason=f"Human review decision: {notes.strip()}",
            actor=reviewer,
            metadata={"source": "human_review", "review_reasons": evaluation.review_reasons},
        )
        evaluation.review_status = Evaluation.REVIEW_APPROVED
        evaluation.reviewed_by = reviewer
        evaluation.reviewed_at = timezone.now()
        evaluation.review_decision = decision
        evaluation.review_notes = notes.strip()
        evaluation.readiness_status = decision
        evaluation.save(update_fields=["review_status", "reviewed_by", "reviewed_at", "review_decision",
                                       "review_notes", "readiness_status", "updated_at"])
        AuditLogService.log(
            user=reviewer,
            action=AuditLogAction.EVALUATION_READINESS_CORRECTED,
            category=AuditLogCategory.EVALUATION,
            description=f"Human review completed for evaluation {evaluation.public_id}: {decision}.",
            resource=evaluation,
            data={"evaluation_id": str(evaluation.public_id), "decision": decision, "notes": notes.strip(),
                  "reasons": evaluation.review_reasons},
        )

        summary = evaluation.session_summaries.order_by("-generated_at").first()
        report = None
        if summary is not None:
            # The decision is recorded and the result released even when no
            # report can be produced - e.g. a staff-started session never
            # captured the candidate's consent, which reports require. That
            # used to raise here, roll the release back and leave the
            # result held forever.
            try:
                report = EvaluationReportService.generate_for_evaluation(evaluation=evaluation, actor=reviewer)
            except EvaluationReportError as exc:
                evaluation.review_notes = f"{evaluation.review_notes} [Report not generated: {exc}]"
                evaluation.save(update_fields=["review_notes", "updated_at"])
            if evaluation.certificate_enabled:
                generate_certificate(evaluation, summary)
        return evaluation, report
