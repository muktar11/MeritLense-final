"""Human-review release safeguard.

An evaluation whose scoring needs a person's judgement (low-confidence or
uncertain interpretation, an unsafe statement, a critical-safety indicator,
or a report flag) is put on hold: its result, report and certificate are not
released to the employer until an admin reviewer records a decision. The
decision is stored as an audited EvaluationReadinessCorrection (the existing,
immutable correction mechanism), so the original automatic decision is kept.
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
        from api.evaluations.models import SessionEvaluationSummary

        reasons = []
        if summary is not None and summary.status == SessionEvaluationSummary.STATUS_REQUIRES_HUMAN_REVIEW:
            reasons.append("Answer interpretation needs human review.")
            for result in evaluation.response_results.select_related("response__evaluation_input_artifact").all():
                artifact = getattr(result.response, "evaluation_input_artifact", None)
                if result.requires_human_review and artifact is not None and artifact.review_reason:
                    reasons.append(artifact.review_reason)
                elif result.requires_human_review and artifact is None:
                    reasons.append("An answer has no AI interpretation yet.")
        if summary is not None and summary.critical_failures:
            reasons.append("Critical safety indicator detected.")
        reasons.extend(cls._critical_question_reasons(evaluation))
        from django.conf import settings

        if summary is not None and getattr(settings, "REVIEW_ALL_RESULTS", False):
            reasons.append("Initial launch period: every result is reviewed before release.")
        # Deduplicate, keep order.
        return list(dict.fromkeys(reasons))

    @classmethod
    def _critical_question_reasons(cls, evaluation):
        """Pooled question banks (GOV2.0+): a Critical question that scored
        0 is a gate of its own. It never turns a result into an automatic
        Not Ready (D-04), but a Ready or Partially Ready result cannot be
        released on the strength of the other answers - a person checks it
        first. Results that are already Not Ready / Incomplete are normal,
        evidenced outcomes and are not held."""
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
        """Called after every scoring run. Holds the evaluation when review
        is needed; never releases an evaluation that is already held, and
        never re-holds one a reviewer has already approved."""
        from api.evaluations.models import Evaluation

        if evaluation.review_status == Evaluation.REVIEW_APPROVED:
            return evaluation
        reasons = cls.review_reasons(evaluation, summary)
        if reasons:
            cls._hold(evaluation, reasons)
        return evaluation

    # Report flags that mean "a person must check this before release":
    # uncertainty in reading the answer, or a safety failure. A missing
    # required point or a competency below threshold is a normal, evidenced
    # Not Ready outcome under the approved rules - it is not held.
    HOLDING_REPORT_FLAGS = {"critical_failure", "low_confidence_interpretation", "transcript_issue", "translation_issue"}

    @classmethod
    def flag_from_report(cls, evaluation, report):
        from api.evaluations.models import Evaluation

        if evaluation.review_status == Evaluation.REVIEW_APPROVED:
            return evaluation
        holding = [f for f in (report.human_review_flags or []) if f.get("flag_type") in cls.HOLDING_REPORT_FLAGS]
        if holding:
            cls._hold(evaluation, [str(f.get("reason") or f.get("flag_type")) for f in holding])
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
