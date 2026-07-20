from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity
from api.evaluations.models import (
    CompetencyEvaluationResult,
    ResponseEvaluationResult,
    SessionEvaluationSummary,
)
from api.reports.models import EvaluationReport


class EvaluationReportError(Exception):
    pass


class EvaluationReportService:
    REPORT_VERSION = "week7-v1"

    @classmethod
    def generate_for_evaluation(cls, *, evaluation, actor):
        cls._log_started(evaluation=evaluation, actor=actor)
        try:
            with transaction.atomic():
                summary = cls._get_summary(evaluation)
                response_results = list(
                    ResponseEvaluationResult.objects.filter(
                        evaluation=evaluation,
                        rule_set=summary.rule_set,
                    )
                    .select_related(
                        "question",
                        "response",
                        "rule_set",
                        "response__translation_artifact",
                        "response__ai_interpretation",
                        "response__evaluation_input_artifact",
                    )
                    .order_by("question__question_order", "created_at")
                )
                competency_results = list(
                    CompetencyEvaluationResult.objects.filter(
                        evaluation=evaluation,
                        rule_set=summary.rule_set,
                    ).order_by("competency_code", "created_at")
                )
                if not response_results:
                    raise EvaluationReportError("Report generation requires response scoring results.")
                if not competency_results:
                    raise EvaluationReportError("Report generation requires competency scoring results.")

                stale_count = cls._mark_previous_reports_stale(evaluation=evaluation, actor=actor)
                report_number = cls._build_report_number(evaluation=evaluation)
                competency_breakdown = cls._build_competency_breakdown(competency_results)
                response_evidence_summary = cls._build_response_evidence(response_results)
                human_review_flags = cls._build_human_review_flags(
                    evaluation=evaluation,
                    summary=summary,
                    response_results=response_results,
                    competency_results=competency_results,
                )
                requires_human_review = bool(
                    human_review_flags or summary.status == SessionEvaluationSummary.STATUS_REQUIRES_HUMAN_REVIEW
                )
                report_payload = cls._build_report_payload(
                    evaluation=evaluation,
                    summary=summary,
                    report_number=report_number,
                    competency_breakdown=competency_breakdown,
                    response_evidence_summary=response_evidence_summary,
                    human_review_flags=human_review_flags,
                    stale_previous_reports=stale_count,
                )
                report = EvaluationReport.objects.create(
                    evaluation=evaluation,
                    session=evaluation.session,
                    candidate=evaluation.candidate,
                    report_number=report_number,
                    report_version=cls.REPORT_VERSION,
                    report_status=EvaluationReport.STATUS_GENERATED,
                    overall_score=summary.total_score,
                    max_score=summary.max_score,
                    overall_percentage=summary.overall_percentage,
                    readiness_status=evaluation.readiness_status,
                    requires_human_review=requires_human_review,
                    scoring_rule_set_name=summary.rule_set.name,
                    scoring_rule_version=summary.rule_set.version,
                    report_payload=report_payload,
                    competency_breakdown=competency_breakdown,
                    response_evidence_summary=response_evidence_summary,
                    human_review_flags=human_review_flags,
                    critical_failures=summary.critical_failures,
                    generated_by=actor if getattr(actor, "is_authenticated", False) else None,
                    generated_at=timezone.now(),
                    last_regenerated_at=timezone.now() if stale_count else None,
                    metadata={
                        "summary_status": summary.status,
                        "evaluated_response_count": summary.evaluated_response_count,
                        "total_response_count": summary.total_response_count,
                        "incomplete_response_count": summary.incomplete_response_count,
                        "stale_previous_reports": stale_count,
                    },
                )
            cls._log_completed(
                evaluation=evaluation,
                actor=actor,
                report=report,
                stale_previous_reports=stale_count,
            )
            return report
        except EvaluationReportError:
            cls._log_failed(evaluation=evaluation, actor=actor)
            raise

    @classmethod
    def export_payload(cls, report):
        return report.report_payload

    @classmethod
    def _get_summary(cls, evaluation):
        if evaluation.session_id is None:
            raise EvaluationReportError("Report generation requires an evaluation linked to an interview session.")
        summary = evaluation.session_summaries.select_related("rule_set").first()
        if summary is None:
            raise EvaluationReportError("Report generation requires a scoring summary.")
        if summary.status not in {
            SessionEvaluationSummary.STATUS_EVALUATED,
            SessionEvaluationSummary.STATUS_REQUIRES_HUMAN_REVIEW,
        }:
            raise EvaluationReportError(
                f"Report generation requires completed scoring. Current summary status: {summary.status}."
            )
        if evaluation.candidate_id is None:
            raise EvaluationReportError("Report generation requires a linked candidate.")
        return summary

    @classmethod
    def _build_report_number(cls, *, evaluation):
        return (
            f"ML-REPORT-{timezone.now().strftime('%Y%m%d%H%M%S%f')}-"
            f"{str(evaluation.public_id).split('-')[0].upper()}"
        )

    @classmethod
    def _mark_previous_reports_stale(cls, *, evaluation, actor):
        reports = list(evaluation.reports.filter(report_status=EvaluationReport.STATUS_GENERATED))
        for report in reports:
            report.report_status = EvaluationReport.STATUS_STALE
            report.last_regenerated_at = timezone.now()
            report.save(update_fields=["report_status", "last_regenerated_at", "updated_at"])
            AuditLogService.log(
                user=actor,
                action=AuditLogAction.PREVIOUS_REPORT_MARKED_STALE,
                category=AuditLogCategory.EVALUATION,
                description=f"Previous report marked stale for evaluation {evaluation.public_id}",
                resource=report,
                data={
                    "evaluation_id": str(evaluation.public_id),
                    "report_id": str(report.public_id),
                    "report_number": report.report_number,
                },
            )
        return len(reports)

    @classmethod
    def _build_competency_breakdown(cls, competency_results):
        rows = []
        for item in competency_results:
            explanation = (
                f"This competency is below threshold because the score is {item.percentage} percent "
                f"and the configured threshold is {item.pass_threshold} percent."
            )
            if item.status != CompetencyEvaluationResult.STATUS_BELOW_THRESHOLD:
                explanation = (
                    f"This competency scored {item.total_score} out of {item.max_score} "
                    f"({item.percentage} percent) across {item.completed_response_count} completed responses."
                )
            rows.append(
                {
                    "competency_code": item.competency_code,
                    "competency_name": item.competency_name,
                    "score": cls._decimal(item.total_score),
                    "max_score": cls._decimal(item.max_score),
                    "percentage": cls._decimal(item.percentage),
                    "status": item.status,
                    "response_count": item.response_count,
                    "completed_response_count": item.completed_response_count,
                    "incomplete_response_count": item.incomplete_response_count,
                    "pass_threshold": cls._decimal(item.pass_threshold),
                    "explanation": explanation,
                }
            )
        return rows

    @classmethod
    def _build_response_evidence(cls, response_results):
        rows = []
        for result in response_results:
            response = result.response
            translation = getattr(response, "translation_artifact", None)
            interpretation = getattr(response, "ai_interpretation", None)
            evaluation_input = getattr(response, "evaluation_input_artifact", None)
            rows.append(
                {
                    "question_id": str(result.question.public_id),
                    "candidate_response_id": str(response.public_id),
                    "question_order": result.question.question_order,
                    "competency_code": result.competency_code,
                    "competency_name": result.competency_name,
                    "question_text": result.question.question_text,
                    "score": cls._decimal(result.score),
                    "max_score": cls._decimal(result.max_score),
                    "percentage": cls._decimal(result.percentage),
                    "observed_indicators": result.observed_indicators,
                    "matched_indicators": result.matched_indicators,
                    "missing_indicators": result.missing_indicators,
                    "required_indicators_passed": result.passed_required_indicators,
                    "critical_failure": result.critical_failure,
                    "requires_human_review": result.requires_human_review,
                    "explanation": result.explanation,
                    "rule_set_version": result.rule_set.version,
                    "traceability": {
                        "transcript_status": response.stt_status,
                        "transcript_language": response.transcript_language,
                        "transcript_reference": {
                            "response_id": str(response.public_id),
                            "has_transcript": bool(response.transcript),
                            "confidence": cls._decimal(response.stt_confidence) if response.stt_confidence is not None else None,
                        },
                        "translation_reference": {
                            "status": getattr(translation, "status", response.translation_status),
                            "provider": getattr(translation, "provider", response.translation_provider),
                            "target_language": getattr(translation, "target_language", response.translation_target_language),
                        },
                        "interpretation_reference": {
                            "status": getattr(interpretation, "status", response.interpretation_status),
                            "provider": getattr(interpretation, "provider", ""),
                            "confidence_score": cls._decimal(getattr(interpretation, "confidence_score", None))
                            if getattr(interpretation, "confidence_score", None) is not None
                            else None,
                        },
                        "evaluation_input_reference": {
                            "source_interpretation_status": getattr(evaluation_input, "source_interpretation_status", ""),
                            "requires_human_review": getattr(evaluation_input, "requires_human_review", False),
                            "review_reason": getattr(evaluation_input, "review_reason", ""),
                        },
                    },
                }
            )
        return rows

    @classmethod
    def _build_human_review_flags(cls, *, evaluation, summary, response_results, competency_results):
        flags = []
        for failure in summary.critical_failures:
            flags.append(
                {
                    "flag_type": "critical_failure",
                    "severity": "high",
                    "source": "scoring_engine",
                    "candidate_response_id": failure.get("response_id"),
                    "message": (
                        f"Critical failure detected for {failure.get('question_code') or 'unknown question'} "
                        f"in {failure.get('competency_code') or 'unknown competency'}."
                    ),
                    "requires_review": True,
                }
            )
        for result in response_results:
            response = result.response
            evaluation_input = getattr(response, "evaluation_input_artifact", None)
            interpretation = getattr(response, "ai_interpretation", None)
            if result.requires_human_review:
                flags.append(
                    {
                        "flag_type": "low_confidence_interpretation",
                        "severity": "medium",
                        "source": "ai_processing",
                        "candidate_response_id": str(response.public_id),
                        "message": getattr(evaluation_input, "review_reason", "") or "Human review is required for this response.",
                        "requires_review": True,
                    }
                )
            transcript_issues = []
            if interpretation is not None:
                transcript_issues = interpretation.structured_output.get("transcript_issues", []) if interpretation.structured_output else []
            if transcript_issues:
                flags.append(
                    {
                        "flag_type": "transcript_issue",
                        "severity": "medium",
                        "source": "ai_processing",
                        "candidate_response_id": str(response.public_id),
                        "message": ", ".join(transcript_issues),
                        "requires_review": True,
                    }
                )
            if response.translation_status == "FAILED":
                flags.append(
                    {
                        "flag_type": "translation_issue",
                        "severity": "medium",
                        "source": "translation",
                        "candidate_response_id": str(response.public_id),
                        "message": response.translation_error or "Translation failed.",
                        "requires_review": True,
                    }
                )
            if not result.passed_required_indicators:
                flags.append(
                    {
                        "flag_type": "missing_required_indicator",
                        "severity": "high",
                        "source": "scoring_engine",
                        "candidate_response_id": str(response.public_id),
                        "message": f"Required indicators were not satisfied for question {result.question.question_order}.",
                        "requires_review": True,
                    }
                )
            if not response.transcript and not response.text_response:
                flags.append(
                    {
                        "flag_type": "incomplete_response",
                        "severity": "medium",
                        "source": "session",
                        "candidate_response_id": str(response.public_id),
                        "message": "Candidate response is incomplete or missing transcript text.",
                        "requires_review": True,
                    }
                )
        for item in competency_results:
            if item.status == CompetencyEvaluationResult.STATUS_BELOW_THRESHOLD:
                flags.append(
                    {
                        "flag_type": "below_threshold_competency",
                        "severity": "medium",
                        "source": "scoring_engine",
                        "candidate_response_id": "",
                        "message": f"{item.competency_name or item.competency_code} is below threshold at {item.percentage} percent.",
                        "requires_review": True,
                    }
                )
        return flags

    @classmethod
    def _build_report_payload(
        cls,
        *,
        evaluation,
        summary,
        report_number,
        competency_breakdown,
        response_evidence_summary,
        human_review_flags,
        stale_previous_reports,
    ):
        candidate = evaluation.candidate
        session = evaluation.session
        return {
            "report_header": {
                "report_number": report_number,
                "report_version": cls.REPORT_VERSION,
                "generated_at": timezone.now().isoformat(),
            },
            "candidate_summary": {
                "candidate_id": str(candidate.public_id),
                "candidate_name": candidate.get_full_name(),
                "job_role": evaluation.candidate_job_role,
                "preferred_language": evaluation.candidate_preferred_language,
            },
            "interview_summary": {
                "session_id": str(session.public_id),
                "evaluation_id": str(evaluation.public_id),
                "session_status": session.status,
                "evaluation_status": evaluation.status,
                "evaluation_tier": session.evaluation_tier,
                "coverage_level": session.coverage_level,
                "package_code": session.package_code,
                "package_name": session.package_name,
                "question_count": session.total_questions,
                "completed_response_count": summary.evaluated_response_count,
                "incomplete_response_count": summary.incomplete_response_count,
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            },
            "overall_result": {
                "total_score": cls._decimal(summary.total_score),
                "max_score": cls._decimal(summary.max_score),
                "overall_percentage": cls._decimal(summary.overall_percentage),
                "readiness_status": evaluation.readiness_status,
                "requires_human_review": bool(human_review_flags),
            },
            "competency_breakdown": competency_breakdown,
            "response_evidence_summary": response_evidence_summary,
            "human_review_flags": human_review_flags,
            "traceability": {
                "scoring_rule_set_name": summary.rule_set.name,
                "scoring_rule_version": summary.rule_set.version,
                "readiness_legal_record_id": cls._get_readiness_record_id(evaluation),
                "audit_reference_type": "evaluation_report",
            },
            "technical_metadata": {
                "summary_status": summary.status,
                "generated_from_stored_outputs": True,
                "stale_previous_reports": stale_previous_reports,
                "generated_by_user_id": str(evaluation.created_by.public_id) if evaluation.created_by_id else None,
            },
        }

    @classmethod
    def _log_started(cls, *, evaluation, actor):
        AuditLogService.log(
            user=actor,
            action=AuditLogAction.REPORT_GENERATION_STARTED,
            category=AuditLogCategory.EVALUATION,
            description=f"Report generation started for evaluation {evaluation.public_id}",
            resource=evaluation,
            data={
                "evaluation_id": str(evaluation.public_id),
                "session_id": str(evaluation.session.public_id) if evaluation.session_id else None,
                "candidate_id": str(evaluation.candidate.public_id),
                "report_version": cls.REPORT_VERSION,
            },
        )

    @classmethod
    def _log_completed(cls, *, evaluation, actor, report, stale_previous_reports):
        action = AuditLogAction.REPORT_REGENERATED if stale_previous_reports else AuditLogAction.REPORT_GENERATION_COMPLETED
        AuditLogService.log(
            user=actor,
            action=action,
            category=AuditLogCategory.EVALUATION,
            description=f"Report generated for evaluation {evaluation.public_id}",
            resource=report,
            data={
                "evaluation_id": str(evaluation.public_id),
                "session_id": str(evaluation.session.public_id) if evaluation.session_id else None,
                "candidate_id": str(evaluation.candidate.public_id),
                "report_id": str(report.public_id),
                "report_number": report.report_number,
                "report_version": report.report_version,
                "scoring_rule_version": report.scoring_rule_version,
            },
        )

    @classmethod
    def _log_failed(cls, *, evaluation, actor):
        AuditLogService.log(
            user=actor,
            action=AuditLogAction.REPORT_GENERATION_FAILED,
            category=AuditLogCategory.EVALUATION,
            description=f"Report generation failed for evaluation {evaluation.public_id}",
            resource=evaluation,
            severity=AuditLogSeverity.WARNING,
            data={
                "evaluation_id": str(evaluation.public_id),
                "session_id": str(evaluation.session.public_id) if evaluation.session_id else None,
                "candidate_id": str(evaluation.candidate.public_id),
                "report_version": cls.REPORT_VERSION,
            },
        )

    @staticmethod
    def _decimal(value):
        if value in (None, ""):
            return None
        if isinstance(value, Decimal):
            return float(value)
        return float(Decimal(str(value)))

    @staticmethod
    def _get_readiness_record_id(evaluation):
        try:
            return str(evaluation.readiness_legal_record.public_id)
        except Exception:
            return None
