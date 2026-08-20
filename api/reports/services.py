import base64
import copy
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity
from api.evaluations.models import (
    CompetencyEvaluationResult,
    ResponseEvaluationResult,
    SessionEvaluationSummary,
)
from api.reports.models import EvaluationReport

try:
    import qrcode
except Exception:  # pragma: no cover - optional runtime dependency
    qrcode = None

try:
    from weasyprint import HTML
except Exception:  # pragma: no cover - optional runtime dependency
    HTML = None


class EvaluationReportError(Exception):
    pass


class EvaluationReportService:
    REPORT_VERSION = "1.3"
    API_SCHEMA_VERSION = "1.3"
    ASSESSMENT_FRAMEWORK_VERSION = "ML-RI-1.0"
    LEGAL_DISCLAIMER = (
        "This report provides decision support only and does not constitute an employment decision. "
        "Final hiring decisions remain with the employer."
    )
    EVALUATION_FLOW_REFERENCE = (
        "Interview Session -> Responses -> AI Processing -> Deterministic Scoring -> Rule Engine -> Evaluation Report"
    )
    CLASSIFICATION = "CONFIDENTIAL - Internal Use Only"
    REPORT_NAME = "MeritLense Workforce Readiness Assessment Report"
    GENERATED_BY = "MeritLense Platform"
    GENERIC_COMPETENCY_LABEL = "Overall Workforce Readiness"
    PUBLIC_VERIFY_FRONTEND_URL = "https://www.meritlense.com"
    LOGO_PATH = Path(__file__).resolve().parents[1] / "evaluations" / "assets" / "meritlense-logo.png"
    _logo_data_uri_cache = None

    @classmethod
    def generate_for_evaluation(cls, *, evaluation, actor):
        cls._log_started(evaluation=evaluation, actor=actor)
        try:
            with transaction.atomic():
                summary = cls._get_summary(evaluation)
                readiness_record = cls._get_readiness_record(evaluation)
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
                pdf_bytes, pdf_hash = cls._render_employer_pdf(
                    report_payload=report_payload,
                    report_number=report_number,
                )
                report_payload["document_integrity"]["hash_value"] = pdf_hash
                report = EvaluationReport(
                    evaluation=evaluation,
                    session=evaluation.session,
                    candidate=evaluation.candidate,
                    report_number=report_number,
                    report_version=cls.REPORT_VERSION,
                    report_status=EvaluationReport.STATUS_ACTIVE,
                    overall_score=summary.total_score,
                    max_score=summary.max_score,
                    overall_percentage=summary.overall_percentage,
                    readiness_status=evaluation.readiness_status,
                    readiness_indicator=cls._resolve_readiness_indicator(evaluation, readiness_record)["value"],
                    readiness_reason=cls._get_readiness_reason(evaluation, readiness_record),
                    override_triggered=cls._get_override_triggered(evaluation, readiness_record),
                    rule_engine_version=cls._get_rule_engine_version(readiness_record),
                    requires_human_review=requires_human_review,
                    scoring_rule_set_name=summary.rule_set.name,
                    scoring_rule_version=summary.rule_set.version,
                    pdf_hash=pdf_hash,
                    report_payload=report_payload,
                    competency_breakdown=competency_breakdown,
                    response_evidence_summary=response_evidence_summary,
                    human_review_flags=human_review_flags,
                    critical_failures=summary.critical_failures,
                    readiness_legal_record=readiness_record,
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
                report.employer_pdf.save(
                    f"{report_number}.pdf",
                    ContentFile(pdf_bytes),
                    save=False,
                )
                report.save()
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
    def export_employer_payload(cls, report):
        allowed_keys = [
            "classification",
            "report_name",
            "report_version",
            "report_status",
            "generated_at",
            "generated_by",
            "legal_disclaimer",
            "document_integrity",
            "assessment_context",
            "executive_summary",
            "assessment_methodology",
            "critical_competency_status",
            "competency_breakdown",
            "risk_indicators",
            "evidence_summary",
            "improvement_plan",
            "data_processing_consent",
            "candidate_snapshot",
            "identity_verification",
            "verification_status",
            "qr_verification_url",
        ]
        payload = {
            key: copy.deepcopy(report.report_payload.get(key))
            for key in allowed_keys
        }
        return cls._sanitize_employer_payload(payload)

    @classmethod
    def build_public_verification_payload(cls, report):
        payload = report.report_payload
        return {
            "report_id": report.report_number,
            "target_role": payload.get("assessment_context", {}).get("target_role"),
            "assessment_date": payload.get("assessment_context", {}).get("assessment_date"),
            "verification_status": cls._derive_qr_verification_status(report.report_status),
            "public_report_status": cls._display_report_status(report.report_status),
            "sha256_hash": report.pdf_hash or payload.get("document_integrity", {}).get("hash_value", ""),
            "verification_timestamp": timezone.now().isoformat(),
        }

    @classmethod
    def _sanitize_employer_payload(cls, payload):
        sanitized = copy.deepcopy(payload or {})

        sanitized.pop("api_schema_version", None)
        sanitized.pop("assessment_framework_version", None)
        sanitized.pop("role_profile_version", None)
        sanitized.pop("rule_engine_version", None)
        sanitized.pop("legal_record_id", None)

        assessment_context = sanitized.get("assessment_context") or {}
        for key in ["candidate_email", "candidate_passport_id", "assessment_session_id", "role_profile_version"]:
            assessment_context.pop(key, None)

        candidate_snapshot = sanitized.get("candidate_snapshot") or {}
        for key in ["email", "passport_id"]:
            candidate_snapshot.pop(key, None)

        executive_summary = sanitized.get("executive_summary") or {}
        readiness_reason = executive_summary.get("readiness_reason")
        if isinstance(readiness_reason, dict):
            executive_summary["readiness_reason"] = {
                "employer_message": readiness_reason.get("employer_message", ""),
            }
        override_details = executive_summary.get("override_details")
        if isinstance(override_details, dict):
            executive_summary["override_details"] = {
                "triggered": bool(override_details.get("triggered")),
            }
        executive_summary.pop("top_source", None)

        identity = sanitized.get("identity_verification") or {}
        identity.pop("method", None)
        identity.pop("timestamp", None)
        identity.pop("face_match_score", None)
        identity.pop("single_face_detected", None)
        identity.pop("liveness_passed", None)
        identity.pop("verification_duration_seconds", None)
        identity.pop("verification_label", None)
        identity.pop("verification_status", None)

        return sanitized

    @classmethod
    def _display_report_status(cls, report_status):
        mapping = {
            EvaluationReport.STATUS_ACTIVE: "Active",
            EvaluationReport.STATUS_SUPERSEDED: "Superseded",
            EvaluationReport.STATUS_REVOKED: "Revoked",
        }
        return mapping.get(report_status, str(report_status or "").title())

    @classmethod
    def render_existing_pdf(cls, report):
        payload = copy.deepcopy(report.report_payload or {})
        if not payload:
            raise EvaluationReportError(
                "This report PDF is unavailable and cannot be rebuilt because its stored payload is missing."
            )
        return cls._render_employer_pdf(
            report_payload=payload,
            report_number=report.report_number,
        )

    @classmethod
    def _get_summary(cls, evaluation):
        if evaluation.session_id is None:
            raise EvaluationReportError("Report generation requires an evaluation linked to an interview session.")
        assessment_status = cls._derive_assessment_status(evaluation)
        if assessment_status != "COMPLETED":
            raise EvaluationReportError(
                f"Report generation is only allowed for completed assessments. Current assessment status: {assessment_status}."
            )
        summary = evaluation.session_summaries.select_related("rule_set").first()
        if summary is None:
            raise EvaluationReportError("Report generation requires a scoring summary.")
        if summary.status not in {
            SessionEvaluationSummary.STATUS_EVALUATED,
            SessionEvaluationSummary.STATUS_REQUIRES_HUMAN_REVIEW,
            SessionEvaluationSummary.STATUS_EVALUATION_FAILED,
        }:
            raise EvaluationReportError(
                f"Report generation requires completed scoring. Current summary status: {summary.status}."
            )
        if evaluation.candidate_id is None:
            raise EvaluationReportError("Report generation requires a linked candidate.")
        return summary

    @classmethod
    def _get_readiness_record(cls, evaluation):
        try:
            return evaluation.readiness_legal_record
        except Exception:
            return None

    @classmethod
    def _build_report_number(cls, *, evaluation):
        return (
            f"ML-REPORT-{timezone.now().strftime('%Y%m%d%H%M%S%f')}-"
            f"{str(evaluation.public_id).split('-')[0].upper()}"
        )

    @classmethod
    def _mark_previous_reports_stale(cls, *, evaluation, actor):
        reports = list(evaluation.reports.filter(report_status=EvaluationReport.STATUS_ACTIVE))
        for report in reports:
            report.report_status = EvaluationReport.STATUS_SUPERSEDED
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
    def _friendly_competency_name(cls, competency_code=None, competency_name=None):
        haystack = " ".join(
            [
                str(competency_code or "").lower(),
                str(competency_name or "").lower(),
            ]
        )
        mappings = [
            (["patient", "safety"], "Patient Safety Awareness"),
            (["hygiene"], "Hygiene Standards"),
            (["clean"], "Hygiene Standards"),
            (["sanitation"], "Hygiene Standards"),
            (["communication"], "Communication Ability"),
            (["language"], "Communication Ability"),
            (["integrity"], "Integrity & Reliability"),
            (["reliability"], "Integrity & Reliability"),
            (["behavior"], "Integrity & Reliability"),
            (["practical", "task"], "Practical Task Execution"),
            (["task", "execution"], "Practical Task Execution"),
            (["task"], "Practical Task Execution"),
            (["safety"], "Safety Awareness"),
            (["knowledge"], "Knowledge & Comprehension"),
            (["logic"], "Knowledge & Comprehension"),
            (["comprehension"], "Knowledge & Comprehension"),
            (["cognitive"], "Knowledge & Comprehension"),
            (["unmapped"], cls.GENERIC_COMPETENCY_LABEL),
        ]
        for tokens, label in mappings:
            if all(token in haystack for token in tokens):
                return label
        return cls._titleize_code(competency_name or competency_code or "Readiness Competency")

    @classmethod
    def _titleize_code(cls, value):
        text = re.sub(r"[_\-]+", " ", str(value or "")).strip()
        return re.sub(r"\s+", " ", text).title() if text else ""

    @classmethod
    def _normalize_text(cls, value):
        if value is None:
            return ""
        if isinstance(value, (list, tuple, set)):
            items = cls._normalize_text_list(value)
            return ", ".join(items)

        text = str(value).strip()
        if not text:
            return ""

        if "," in text:
            raw_parts = text.split(",")
            non_empty_parts = [part for part in raw_parts if part != ""]
            if non_empty_parts:
                single_char_ratio = sum(len(part.strip()) <= 1 for part in non_empty_parts) / len(non_empty_parts)
                if single_char_ratio >= 0.75:
                    text = "".join(raw_parts)

        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"\.(?:\s*[,.;:!?])+", ".", text)
        text = re.sub(r",(?:\s*[,;:!?])+", ",", text)
        text = re.sub(r"([;:!?])(?:\s*[,.;:!?])+", r"\1", text)
        text = re.sub(
            r"additional evidence is needed for identify hazards?",
            "Additional evidence is needed to identify hazards",
            text,
            flags=re.IGNORECASE,
        )
        return text

    @classmethod
    def _normalize_text_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            normalized = cls._normalize_text(value)
            return [normalized] if normalized else []

        items = list(value) if isinstance(value, (list, tuple, set)) else [value]
        stringified = [str(item) for item in items if item not in (None, "")]
        if stringified:
            single_char_ratio = sum(len(item.strip()) <= 1 for item in stringified) / len(stringified)
            if single_char_ratio >= 0.75:
                rebuilt = cls._normalize_text(",".join(stringified))
                return [rebuilt] if rebuilt else []

        cleaned = []
        seen = set()
        for item in items:
            normalized = cls._normalize_text(item)
            if normalized and normalized not in seen:
                seen.add(normalized)
                cleaned.append(normalized)
        return cleaned

    @classmethod
    def _join_evidence_sentences(cls, items):
        # Each item here is already a complete, punctuated sentence (e.g.
        # "X is below the required threshold.") - joining with ", " produces
        # a "sentence., Next sentence." run-on, so these join with a space
        # instead, unlike short comma-separated phrase lists elsewhere.
        return " ".join(cls._normalize_text_list(items))

    @classmethod
    def _strength_statement(cls, label):
        return f"Strong {label.lower()}"

    @classmethod
    def _risk_statement(cls, label):
        return f"Readiness gap identified in {label.lower()}"

    @classmethod
    def _humanize_indicator_phrase(cls, value):
        text = cls._normalize_text(value)
        text = re.sub(r"[_\-]+", " ", text).strip(" ,.;:")
        replacements = {
            "identify hazard": "identify hazards",
        }
        lowered = text.lower()
        return replacements.get(lowered, lowered or text)

    @classmethod
    def _format_indicator_list(cls, items):
        cleaned = []
        seen = set()
        for item in items:
            phrase = cls._humanize_indicator_phrase(item)
            if phrase and phrase not in seen:
                seen.add(phrase)
                cleaned.append(phrase)
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]} and {cleaned[1]}"
        return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"

    @classmethod
    def _employer_evidence_finding(cls, item):
        matched = cls._normalize_text_list(item.get("matched_indicators") or item.get("observed_indicators") or [])
        missing = cls._normalize_text_list(item.get("missing_indicators") or [])
        if matched and missing:
            return (
                f"Candidate demonstrated {cls._format_indicator_list(matched)}. "
                f"Additional evidence is needed to {cls._format_indicator_list(missing)}."
            )
        if missing:
            return f"Additional evidence is needed to {cls._format_indicator_list(missing)}."
        if matched:
            return f"Candidate demonstrated {cls._format_indicator_list(matched)}."
        return cls._normalize_text(item.get("explanation") or "Readiness gap identified from the candidate response.")

    @classmethod
    def _build_competency_breakdown(cls, competency_results):
        rows = []
        for item in competency_results:
            display_name = cls._friendly_competency_name(item.competency_code, item.competency_name)
            max_score = cls._decimal(item.max_score)
            completed_response_count = item.completed_response_count or 0
            if max_score <= 0:
                assessment_status = "NOT_ASSESSED"
                score_display = "Not Assessed"
                explanation = "This competency was not assessed in this session."
            elif completed_response_count <= 0:
                assessment_status = "INSUFFICIENT_EVIDENCE"
                score_display = "Insufficient Evidence"
                explanation = "There was insufficient completed evidence to assess this competency."
            elif item.status == CompetencyEvaluationResult.STATUS_BELOW_THRESHOLD:
                assessment_status = item.status
                score_display = f"{cls._decimal(item.total_score)}/{max_score} ({cls._decimal(item.percentage)}%)"
                explanation = (
                    f"This competency is below threshold because the score is {item.percentage} percent "
                    f"and the configured threshold is {item.pass_threshold} percent."
                )
            else:
                assessment_status = item.status
                score_display = f"{cls._decimal(item.total_score)}/{max_score} ({cls._decimal(item.percentage)}%)"
                explanation = (
                    f"This competency scored {item.total_score} out of {item.max_score} "
                    f"({item.percentage} percent) across {item.completed_response_count} completed responses."
                )
            rows.append(
                {
                    "competency_code": item.competency_code,
                    "competency_name": item.competency_name,
                    "display_name": display_name,
                    "score": cls._decimal(item.total_score),
                    "max_score": max_score,
                    "percentage": cls._decimal(item.percentage),
                    "status": item.status,
                    "assessment_status": assessment_status,
                    "score_display": score_display,
                    "response_count": item.response_count,
                    "completed_response_count": item.completed_response_count,
                    "incomplete_response_count": item.incomplete_response_count,
                    "pass_threshold": cls._decimal(item.pass_threshold),
                    "explanation": explanation,
                }
            )
        return rows

    @classmethod
    def _build_evaluator_rating(cls, evaluation):
        """The evaluator's manual 0-100 ratings on the 4 fixed pools, pulled
        live at generation time (same idiom as identity_verified being
        pulled live from the session) - None if no rating has been
        submitted yet. Snapshotted into report_payload like everything
        else here, so a later rating/resubmission only shows up on a
        freshly generated report, consistent with report immutability."""
        rating = getattr(evaluation, "evaluator_rating", None)
        if rating is None:
            return None
        from api.evaluations.evaluator_rating_services import calculate_response_consistency

        return {
            "safety_awareness": rating.safety_awareness,
            "behavior_integrity": rating.behavior_integrity,
            "psych_professional": rating.psych_professional,
            "task_execution": rating.task_execution,
            "consistency": calculate_response_consistency(evaluation),
            "rated_by_name": rating.rated_by.get_full_name() if rating.rated_by else None,
            "rated_at": rating.rated_at.isoformat() if rating.rated_at else None,
        }

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
                    "question_type": (
                        getattr(result.rule, "question_type", "")
                        or getattr(getattr(result.question, "question_template", None), "question_type", "")
                    ),
                    "competency_code": result.competency_code,
                    "competency_name": result.competency_name,
                    "display_name": cls._friendly_competency_name(result.competency_code, result.competency_name),
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
            competency = cls._friendly_competency_name(
                failure.get("competency_code"),
                failure.get("competency_name"),
            )
            flags.append(
                {
                    "flag_type": "critical_failure",
                    "severity": "high",
                    "source": "scoring_engine",
                    "candidate_response_id": failure.get("response_id"),
                    "message": cls._normalize_text(
                        failure.get("reason") or f"Required readiness evidence was not met for {competency}."
                    ),
                    "requires_review": True,
                }
            )
        for result in response_results:
            response = result.response
            evaluation_input = getattr(response, "evaluation_input_artifact", None)
            interpretation = getattr(response, "ai_interpretation", None)
            review_message = cls._normalize_text(getattr(evaluation_input, "review_reason", ""))
            if "confidence" in review_message.lower() or "threshold" in review_message.lower():
                review_message = "Response interpretation requires manual review."
            if result.requires_human_review:
                flags.append(
                    {
                        "flag_type": "low_confidence_interpretation",
                        "severity": "medium",
                        "source": "ai_processing",
                        "candidate_response_id": str(response.public_id),
                        "message": review_message or "Human review is required for this response.",
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
                        "message": ", ".join(cls._normalize_text_list(transcript_issues)),
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
                        "message": cls._normalize_text(response.translation_error)
                        or "Response translation requires manual review.",
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
                        "message": (
                            f"Additional evidence is required for assessment question {result.question.question_order}."
                        ),
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
                        "message": "No usable response was captured for this assessment item.",
                        "requires_review": True,
                    }
                )
        for item in competency_results:
            if item.status == CompetencyEvaluationResult.STATUS_BELOW_THRESHOLD:
                competency = cls._friendly_competency_name(item.competency_code, item.competency_name)
                flags.append(
                    {
                        "flag_type": "below_threshold_competency",
                        "severity": "medium",
                        "source": "scoring_engine",
                        "candidate_response_id": "",
                        "message": f"{competency} is below the required threshold.",
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
        readiness_record = cls._get_readiness_record(evaluation)
        readiness_indicator = cls._resolve_readiness_indicator(evaluation, readiness_record)
        readiness_reason = cls._get_readiness_reason(evaluation, readiness_record)
        override_triggered = cls._get_override_triggered(evaluation, readiness_record)
        rule_engine_version = cls._get_rule_engine_version(readiness_record)
        assessment_status = cls._derive_assessment_status(evaluation)
        generated_at = timezone.now()
        top_strengths, top_risks = cls._derive_top_strengths_and_risks(
            competency_breakdown=competency_breakdown,
            critical_failures=summary.critical_failures,
        )
        evidence_summary = cls._build_evidence_summary(
            response_evidence_summary=response_evidence_summary,
            competency_breakdown=competency_breakdown,
            critical_failures=summary.critical_failures,
        )
        risk_indicators = cls._build_risk_indicators(
            competency_breakdown=competency_breakdown,
            response_evidence_summary=response_evidence_summary,
            human_review_flags=human_review_flags,
            critical_failures=summary.critical_failures,
        )
        improvement_plan = cls._build_improvement_plan(
            readiness_indicator=readiness_indicator,
            competency_breakdown=competency_breakdown,
            risk_indicators=risk_indicators,
            critical_failures=summary.critical_failures,
        )
        employer_message = cls._build_employer_readiness_message(
            readiness_indicator=readiness_indicator,
            risk_indicators=risk_indicators,
            override_triggered=override_triggered,
        )
        suggested_action, suggested_action_display = cls._derive_suggested_action(
            readiness_indicator=readiness_indicator,
            override_triggered=override_triggered,
        )
        assessment_completeness = cls._derive_assessment_completeness(summary)
        reliability, reliability_factors = cls._derive_evaluation_reliability(
            session=session,
            summary=summary,
            human_review_flags=human_review_flags,
            response_evidence_summary=response_evidence_summary,
        )
        assessment_quality = cls._derive_assessment_quality(
            session=session,
            summary=summary,
            human_review_flags=human_review_flags,
            response_evidence_summary=response_evidence_summary,
        )
        consent_summary = cls._build_consent_summary(session)
        identity_verification = cls._build_identity_verification_summary(session)
        candidate_snapshot = cls._build_candidate_snapshot(candidate, session)
        evaluation_bands = cls._build_evaluation_bands(
            competency_breakdown=competency_breakdown,
            response_evidence_summary=response_evidence_summary,
            overall_percentage=summary.overall_percentage,
        )
        assessment_methodology = cls._build_assessment_methodology()
        critical_competency_status = cls._build_critical_competency_status(
            competency_breakdown=competency_breakdown,
            risk_indicators=risk_indicators,
        )
        role_fit = cls._build_role_fit_summary(
            evaluation=evaluation,
            overall_percentage=summary.overall_percentage,
            evaluation_bands=evaluation_bands,
        )
        audit_log = cls._build_audit_log_stub(evaluation=evaluation, generated_at=generated_at)
        qr_verification_url = cls._build_qr_verification_url(report_number=report_number)

        payload = {
            "classification": cls.CLASSIFICATION,
            "report_name": cls.REPORT_NAME,
            "report_version": cls.REPORT_VERSION,
            "api_schema_version": cls.API_SCHEMA_VERSION,
            "assessment_framework_version": cls.ASSESSMENT_FRAMEWORK_VERSION,
            "rule_engine_version": rule_engine_version,
            "role_profile_version": cls._derive_role_profile_version(session),
            "report_status": EvaluationReport.STATUS_ACTIVE,
            "generated_at": generated_at.isoformat(),
            "generated_by": cls.GENERATED_BY,
            "legal_record_id": cls._get_readiness_record_id(evaluation),
            "legal_disclaimer": cls.LEGAL_DISCLAIMER,
            "document_integrity": {
                "hash_algorithm": "SHA-256",
                "hash_value": "",
            },
            "assessment_context": {
                "candidate_reference": cls._build_candidate_reference(candidate),
                "candidate_name": candidate_snapshot["full_name"],
                "candidate_email": candidate_snapshot["email"],
                "candidate_passport_id": candidate_snapshot["passport_id"],
                "assessment_session_id": str(session.public_id),
                "assessment_status": assessment_status,
                "target_role": session.role_name or evaluation.get_candidate_job_role_display(),
                "role_profile_version": cls._derive_role_profile_version(session),
                "assessment_type": "Pre-Employment Readiness",
                "assessment_language": cls._display_language(session.ui_language or evaluation.candidate_preferred_language),
                "assessment_mode": "Guided Digital Simulation",
                "assessment_date": (session.ended_at or generated_at).date().isoformat(),
                "assessment_duration_minutes": cls._derive_assessment_duration_minutes(session),
                "assessment_completeness": assessment_completeness,
                "assessment_quality": assessment_quality,
                "assessment_coverage": cls._derive_assessment_coverage(session),
            },
            "executive_summary": {
                "readiness_indicator": readiness_indicator,
                "overall_score": cls._rounded_whole(summary.overall_percentage),
                "overall_score_display": cls._derive_overall_score_display(
                    overall_percentage=summary.overall_percentage,
                    assessment_completeness=assessment_completeness,
                ),
                "overall_score_available": assessment_completeness >= 100,
                "readiness_reason": {
                    "employer_message": employer_message,
                    "internal_reason": readiness_reason,
                },
                "override_triggered": override_triggered,
                "override_details": {
                    "triggered": override_triggered,
                    "rule": "critical_requirement_override" if override_triggered else None,
                    "impact": "readiness_indicator_takes_precedence_over_overall_score" if override_triggered else None,
                },
                "top_strengths": top_strengths,
                "top_risks": top_risks,
                "top_source": "Rule Engine - competency score ranking",
                "suggested_action": suggested_action,
                "suggested_action_display": suggested_action_display,
                "assessment_scope": "Pre-employment Workforce Readiness Only",
                "evaluation_reliability": reliability,
                "reliability_factors": reliability_factors,
            },
            "assessment_methodology": assessment_methodology,
            "critical_competency_status": critical_competency_status,
            "risk_indicators": risk_indicators,
            "evidence_summary": evidence_summary,
            "improvement_plan": improvement_plan,
            "data_processing_consent": consent_summary,
            "candidate_snapshot": candidate_snapshot,
            "identity_verification": identity_verification,
            "transcript_report": {
                "evaluation_bands": evaluation_bands,
                "role_fit": role_fit,
                "privacy_commitments": cls._build_privacy_commitments(),
                "compliance_badges": cls._build_compliance_badges(),
            },
            "verification_status": cls._derive_qr_verification_status(EvaluationReport.STATUS_ACTIVE),
            "qr_verification_url": qr_verification_url,
            "candidate_id": str(candidate.public_id),
            "overall_score": cls._decimal(summary.overall_percentage),
            "competency_breakdown": competency_breakdown,
            "evaluator_rating": cls._build_evaluator_rating(evaluation),
            "critical_failures": summary.critical_failures,
            "human_review_flags": human_review_flags,
            "audit_log": audit_log,
            "score_readiness_policy": {
                "overall_score_note": "overall_score represents aggregate performance only.",
                "readiness_indicator_note": "readiness_indicator is the authoritative output from the Rule Engine and may override overall_score.",
            },
            "evaluation_flow_reference": cls.EVALUATION_FLOW_REFERENCE,
            "metadata": {
                "stale_previous_reports": stale_previous_reports,
                "summary_status": summary.status,
                "scoring_rule_set_name": summary.rule_set.name,
                "scoring_rule_version": summary.rule_set.version,
                "requires_human_review": bool(human_review_flags),
            },
        }
        payload["document_integrity"]["hash_value"] = cls._calculate_document_hash(payload)
        return payload

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

    @classmethod
    def _derive_assessment_status(cls, evaluation):
        session = evaluation.session
        if evaluation.status == "COMPLETED" and session and session.status == "COMPLETED":
            return "COMPLETED"
        if session and session.status in {"FAILED", "EXPIRED"}:
            return "INVALIDATED"
        if evaluation.status == "CANCELLED":
            return "ABANDONED"
        return "IN_PROGRESS"

    @classmethod
    def _resolve_readiness_indicator(cls, evaluation, readiness_record):
        raw_value = ""
        if readiness_record is not None:
            raw_value = readiness_record.readiness_indicator or ""
        elif evaluation.readiness_status == "READY":
            raw_value = "جاهز"
        elif evaluation.readiness_status == "NOT_READY":
            raw_value = "غير جاهز"
        else:
            raw_value = "متوسط"

        normalized = str(raw_value).strip().upper()
        if raw_value in {"جاهز", "READY"} or normalized == "READY":
            return {"value": "جاهز", "display": "Ready", "code": "READY", "level": 1}
        if raw_value in {"متوسط", "PARTIALLY_READY"} or normalized == "PARTIALLY_READY":
            return {"value": "جاهزية جزئية", "display": "Partially Ready", "code": "PARTIALLY_READY", "level": 2}
        if raw_value in {"غير جاهز", "NOT_READY", "توجد فجوات جاهزية"} or normalized == "NOT_READY":
            return {
                "value": "توجد فجوات جاهزية",
                "display": "Readiness Gaps Identified",
                "code": "NOT_READY",
                "level": 3,
            }
        return {"value": "جاهزية جزئية", "display": "Partially Ready", "code": "PARTIALLY_READY", "level": 2}

    @classmethod
    def _derive_top_strengths_and_risks(cls, *, competency_breakdown, critical_failures):
        generic_label = cls.GENERIC_COMPETENCY_LABEL
        risk_labels = set()
        risks = []
        for failure in critical_failures:
            label = cls._friendly_competency_name(
                failure.get("competency_code") or failure.get("topic"),
                failure.get("competency_name"),
            )
            if label and label != generic_label:
                risk_labels.add(label)
                statement = cls._risk_statement(label)
                if statement not in risks:
                    risks.append(statement)

        for item in competency_breakdown:
            label = item.get("display_name") or item.get("competency_name") or item.get("competency_code")
            if (
                label
                and label != generic_label
                and item.get("assessment_status") == CompetencyEvaluationResult.STATUS_BELOW_THRESHOLD
            ):
                risk_labels.add(label)
                statement = cls._risk_statement(label)
                if statement not in risks:
                    risks.append(statement)

        ordered = sorted(
            competency_breakdown,
            key=lambda item: item.get("percentage") if item.get("percentage") is not None else -1,
            reverse=True,
        )
        strengths = []
        for item in ordered:
            if not item:
                continue
            label = item.get("display_name") or item.get("competency_name") or item.get("competency_code")
            percentage = float(item.get("percentage") or 0)
            threshold = float(item.get("pass_threshold") or 0)
            if (
                not label
                or label == generic_label
                or label in risk_labels
                or item.get("completed_response_count", 0) <= 0
                or item.get("assessment_status")
                not in {
                    CompetencyEvaluationResult.STATUS_EVALUATED,
                    CompetencyEvaluationResult.STATUS_MEETS_THRESHOLD,
                }
                or float(item.get("max_score") or 0) <= 0
                or percentage <= 0
                or percentage < threshold
            ):
                continue
            statement = cls._strength_statement(label)
            if statement not in strengths:
                strengths.append(statement)
            if len(strengths) >= 3:
                break
        if not strengths:
            strengths = ["No significant strengths identified in this assessment."]

        weakest = sorted(
            competency_breakdown,
            key=lambda item: item.get("percentage") if item.get("percentage") is not None else 101,
        )
        for item in weakest:
            label = item.get("display_name") or item.get("competency_name") or item.get("competency_code")
            percentage = float(item.get("percentage") or 0)
            threshold = float(item.get("pass_threshold") or 0)
            if (
                not label
                or label == generic_label
                or item.get("completed_response_count", 0) <= 0
                or item.get("assessment_status") != CompetencyEvaluationResult.STATUS_BELOW_THRESHOLD
                or percentage >= threshold
            ):
                continue
            statement = cls._risk_statement(label)
            if statement not in risks:
                risks.append(statement)
            if len(risks) >= 3:
                break
        return strengths[:3], risks[:3]

    @classmethod
    def _build_evidence_summary(cls, *, response_evidence_summary, competency_breakdown, critical_failures):
        items = []
        for failure in critical_failures:
            items.append(
                {
                    "category": cls._friendly_competency_name(
                        failure.get("competency_code"),
                        failure.get("competency_name"),
                    ),
                    "finding": cls._normalize_text(
                        failure.get("reason") or "Critical readiness requirement was not met."
                    ),
                    "source": "Assessment requirement",
                    "severity": "High",
                }
            )
        for item in response_evidence_summary:
            if item.get("missing_indicators"):
                items.append(
                    {
                        "category": item.get("display_name") or item.get("competency_name") or item.get("competency_code"),
                        "finding": cls._employer_evidence_finding(item),
                        "source": f"Simulation {item.get('question_order')}",
                        "severity": "High" if item.get("critical_failure") else "Medium",
                    }
                )
        deduped = []
        seen = set()
        for item in items:
            key = (item["category"], item["finding"], item["source"], item["severity"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:10]

    @classmethod
    def _build_risk_indicators(cls, *, competency_breakdown, response_evidence_summary, human_review_flags, critical_failures):
        def competency_percentage(name_matches):
            for item in competency_breakdown:
                code = (item.get("competency_code") or "").lower()
                name = (item.get("competency_name") or "").lower()
                if any(token in code or token in name for token in name_matches):
                    return item.get("percentage") or 0, item
            return 0, None

        safety_pct, safety_item = competency_percentage(["safety"])
        hygiene_pct, hygiene_item = competency_percentage(["hygiene", "clean", "sanitation"])
        communication_pct, communication_item = competency_percentage(["communication", "language"])
        integrity_pct, integrity_item = competency_percentage(["integrity", "reliability", "behavior"])

        interpretation_confidences = []
        for item in response_evidence_summary:
            confidence = (
                ((item.get("traceability") or {}).get("interpretation_reference") or {}).get("confidence_score")
            )
            if confidence is not None:
                interpretation_confidences.append(float(confidence))
        avg_language_quality = sum(interpretation_confidences) / len(interpretation_confidences) if interpretation_confidences else 1.0

        integrity_flag_messages = [
            cls._normalize_text(flag.get("message"))
            for flag in human_review_flags
            if flag.get("flag_type") in {"critical_failure", "low_confidence_interpretation", "transcript_issue"}
        ]

        return {
            "note": "Risk scores use a 0 to 100 scale. Higher scores indicate greater readiness concern.",
            "safety_risk": cls._risk_block(
                competency_item=safety_item,
                competency_percentage=safety_pct,
                has_critical_failure=any("safety" in str((f.get("competency_code") or f.get("competency_name") or "")).lower() for f in critical_failures),
                fallback_evidence=[f.get("reason") for f in critical_failures],
            ),
            "hygiene_risk": cls._risk_block(
                competency_item=hygiene_item,
                competency_percentage=hygiene_pct,
                has_critical_failure=any("hygiene" in str((f.get("competency_code") or f.get("competency_name") or "")).lower() for f in critical_failures),
                fallback_evidence=[],
            ),
            "communication_risk": cls._communication_risk_block(
                avg_language_quality=avg_language_quality,
                competency_item=communication_item,
                competency_percentage=communication_pct,
            ),
            "integrity_risk": cls._integrity_risk_block(
                integrity_item=integrity_item,
                integrity_percentage=integrity_pct,
                human_review_flags=human_review_flags,
                integrity_flag_messages=integrity_flag_messages,
            ),
        }

    @classmethod
    def _risk_block(cls, *, competency_item, competency_percentage, has_critical_failure, fallback_evidence):
        if not competency_item or competency_item.get("assessment_status") in {"NOT_ASSESSED", "INSUFFICIENT_EVIDENCE"}:
            return {
                "level": "Not Assessed",
                "risk_score": None,
                "risk_score_display": "Not Assessed",
                "evidence": ["Insufficient evidence was available to assess this risk category."],
            }
        risk_score = max(0, min(100, int(round(100 - float(competency_percentage or 0)))))
        if has_critical_failure or competency_percentage < 40:
            level = "High"
        elif competency_percentage <= 70:
            level = "Medium"
        else:
            level = "Low"
        evidence = []
        if competency_item and competency_item.get("status") == "BELOW_THRESHOLD":
            label = competency_item.get("display_name") or competency_item.get("competency_name") or competency_item.get("competency_code")
            evidence.append(f"{label} is below the required threshold.")
        evidence.extend(cls._normalize_text_list(fallback_evidence))
        return {
            "level": level,
            "risk_score": risk_score,
            "risk_score_display": str(risk_score),
            "evidence": evidence[:5],
        }

    @classmethod
    def _communication_risk_block(cls, *, avg_language_quality, competency_item, competency_percentage):
        if not competency_item or competency_item.get("assessment_status") in {"NOT_ASSESSED", "INSUFFICIENT_EVIDENCE"}:
            return {
                "level": "Not Assessed",
                "risk_score": None,
                "risk_score_display": "Not Assessed",
                "evidence": ["Insufficient evidence was available to assess communication risk."],
            }
        risk_score = max(0, min(100, int(round((1 - avg_language_quality) * 100))))
        if avg_language_quality < 0.5:
            level = "High"
        elif avg_language_quality <= 0.75:
            level = "Medium"
        else:
            level = "Low"
        evidence = []
        if competency_item and competency_percentage < 70:
            label = competency_item.get("display_name") or competency_item.get("competency_name") or competency_item.get("competency_code")
            evidence.append(f"{label} requires additional communication support.")
        return {"level": level, "risk_score": risk_score, "risk_score_display": str(risk_score), "evidence": evidence}

    @classmethod
    def _integrity_risk_block(cls, *, integrity_item, integrity_percentage, human_review_flags, integrity_flag_messages):
        if not integrity_item or integrity_item.get("assessment_status") in {"NOT_ASSESSED", "INSUFFICIENT_EVIDENCE"}:
            evidence = cls._normalize_text_list(integrity_flag_messages)[:5]
            return {
                "level": "Not Assessed",
                "risk_score": None,
                "risk_score_display": "Not Assessed",
                "evidence": evidence or ["Insufficient evidence was available to assess integrity risk."],
            }
        has_integrity_flag = any(flag.get("requires_review") for flag in human_review_flags)
        risk_score = max(0, min(100, int(round(100 - float(integrity_percentage or 92)))))
        if has_integrity_flag:
            level = "High"
        elif human_review_flags:
            level = "Medium"
        else:
            level = "Low"
        evidence = cls._normalize_text_list(integrity_flag_messages)[:5]
        if integrity_item and integrity_item.get("status") == "BELOW_THRESHOLD":
            label = integrity_item.get("display_name") or integrity_item.get("competency_name") or integrity_item.get("competency_code")
            evidence.append(f"{label} is below the required threshold.")
        return {
            "level": level,
            "risk_score": risk_score,
            "risk_score_display": str(risk_score),
            "evidence": evidence[:5],
        }

    @classmethod
    def _build_improvement_plan(cls, *, readiness_indicator, competency_breakdown, risk_indicators, critical_failures):
        if readiness_indicator["code"] == "READY":
            return []
        items = []
        priority_map = {"High": "High", "Medium": "Medium", "Low": "Low"}
        for block_name, risk in risk_indicators.items():
            if block_name == "note" or risk["level"] not in {"High", "Medium"}:
                continue
            competency = cls._friendly_competency_name(block_name.replace("_risk", ""))
            gap = cls._join_evidence_sentences(risk.get("evidence") or []) or f"{competency} readiness gap identified."
            recommended_action = "Targeted critical training" if risk["level"] == "High" else "Focused training"
            items.append(
                {
                    "competency": competency,
                    "gap": gap,
                    "recommended_action": recommended_action,
                    "priority": priority_map[risk["level"]],
                }
            )
        for failure in critical_failures:
            competency = cls._friendly_competency_name(
                failure.get("competency_code"),
                failure.get("competency_name"),
            )
            if any(item["competency"] == competency for item in items):
                continue
            items.append(
                {
                    "competency": competency,
                    "gap": cls._normalize_text(failure.get("reason") or "Critical readiness requirement not met."),
                    "recommended_action": "Basic safety training" if "safety" in competency.lower() else "Targeted remedial training",
                    "priority": "High",
                }
            )
        return items[:5]

    @classmethod
    def _build_employer_readiness_message(cls, *, readiness_indicator, risk_indicators, override_triggered):
        if override_triggered:
            return "A critical readiness requirement was not met."
        if risk_indicators["safety_risk"]["level"] == "High":
            return "Safety readiness gap identified"
        if readiness_indicator["code"] == "READY":
            return "Candidate meets the assessment criteria for this role."
        if readiness_indicator["code"] == "PARTIALLY_READY":
            return "Readiness gaps identified. Training in specific areas is recommended."
        return "Readiness gaps identified. Re-evaluation after training is recommended."

    @classmethod
    def _derive_suggested_action(cls, *, readiness_indicator, override_triggered):
        if readiness_indicator["code"] == "READY" and not override_triggered:
            return "PROCEED", "Proceed"
        if readiness_indicator["code"] == "PARTIALLY_READY" and not override_triggered:
            return "CONSIDER_TRAINING", "Consider Training"
        if override_triggered:
            return "RE_EVALUATE_CRITICAL", "Re-evaluate after Critical Training"
        return "RE_EVALUATE", "Re-evaluate"

    @classmethod
    def _derive_evaluation_reliability(cls, *, session, summary, human_review_flags, response_evidence_summary):
        factors = []
        completeness = cls._derive_assessment_completeness(summary)
        stt_scores = [
            item.get("traceability", {}).get("transcript_reference", {}).get("confidence")
            for item in response_evidence_summary
            if item.get("traceability", {}).get("transcript_reference", {}).get("confidence") is not None
        ]
        avg_stt = sum(float(score) for score in stt_scores) / len(stt_scores) if stt_scores else 0.9
        if completeness >= 90:
            factors.append("Complete interview")
        else:
            factors.append(f"Interview completeness {completeness}%")
        if session.task_observation_enabled:
            factors.append("Practical observation included")
        if avg_stt >= 0.8:
            factors.append("Good audio quality")
        else:
            factors.append("Audio quality requires review")
        if not human_review_flags:
            factors.append("High response consistency")
        else:
            factors.append("Response consistency requires review")
        if human_review_flags:
            return "Medium", factors
        if completeness >= 90 and avg_stt >= 0.8:
            return "High", factors
        return "Low", factors

    @classmethod
    def _derive_assessment_quality(cls, *, session, summary, human_review_flags, response_evidence_summary):
        completeness = cls._derive_assessment_completeness(summary)
        duration_minutes = cls._derive_assessment_duration_minutes(session)
        target_duration = (
            getattr(getattr(session, "package_session_config", None), "duration_minutes", None)
            or getattr(getattr(session, "config", None), "duration_minutes", None)
            or 0
        )
        duration_ratio = (duration_minutes / target_duration) if target_duration else 1
        stt_scores = [
            item.get("traceability", {}).get("transcript_reference", {}).get("confidence")
            for item in response_evidence_summary
            if item.get("traceability", {}).get("transcript_reference", {}).get("confidence") is not None
        ]
        avg_stt = sum(float(score) for score in stt_scores) / len(stt_scores) if stt_scores else 0.9

        if completeness >= 90 and avg_stt >= 0.85 and duration_ratio >= 0.5 and not human_review_flags:
            return "Excellent"
        if completeness >= 75 and avg_stt >= 0.7 and duration_ratio >= 0.35:
            return "Good"
        return "Limited"

    @classmethod
    def _build_assessment_methodology(cls):
        return [
            {
                "domain": "Safety Awareness",
                "evaluated": "Emergency response, hazard identification, protocol knowledge",
                "method": "Scenario-based Q&A + Simulation",
            },
            {
                "domain": "Hygiene & Standards",
                "evaluated": "Cleanliness procedures, cross-contamination prevention",
                "method": "Scenario-based Q&A",
            },
            {
                "domain": "Communication Ability",
                "evaluated": "Language clarity, instruction following, response quality",
                "method": "Communication Assessment Module + Speech Processing",
            },
            {
                "domain": "Practical Task Execution",
                "evaluated": "Step sequence, completion rate, time management",
                "method": "Task Observation Module",
            },
            {
                "domain": "Behavioral Indicators",
                "evaluated": "Integrity, reliability, consistency under pressure",
                "method": "Behavioral Assessment Module",
            },
        ]

    @classmethod
    def _build_critical_competency_status(cls, *, competency_breakdown, risk_indicators):
        categories = [
            ("Safety", ["safety"]),
            ("Hygiene", ["hygiene", "clean", "sanitation"]),
            ("Communication", ["communication", "language"]),
            ("Integrity", ["integrity", "reliability", "behavior"]),
        ]
        items = []
        for label, tokens in categories:
            competency = None
            for item in competency_breakdown:
                haystack = " ".join(
                    [
                        str(item.get("competency_code") or "").lower(),
                        str(item.get("competency_name") or "").lower(),
                    ]
                )
                if any(token in haystack for token in tokens):
                    competency = item
                    break
            risk = risk_indicators.get(f"{label.lower()}_risk", {}) if isinstance(risk_indicators, dict) else {}
            risk_level = risk.get("level", "Low")
            assessment_status = competency.get("assessment_status") if competency else "NOT_ASSESSED"
            if assessment_status in {"NOT_ASSESSED", "INSUFFICIENT_EVIDENCE"}:
                status_label = "Not Assessed" if assessment_status == "NOT_ASSESSED" else "Insufficient Evidence"
                tone = "neutral"
                score_display = status_label
            elif risk_level == "Low" and competency.get("status") != "BELOW_THRESHOLD":
                status_label = "Meets Standard"
                tone = "good"
                score_display = competency.get("score_display")
            elif risk_level == "Medium":
                status_label = "Review Needed"
                tone = "warn"
                score_display = competency.get("score_display")
            else:
                status_label = "Attention Required"
                tone = "danger"
                score_display = competency.get("score_display") if competency else "Not Assessed"
            items.append(
                {
                    "label": label,
                    "status_label": status_label,
                    "tone": tone,
                    "risk_level": risk_level,
                    "risk_score": risk.get("risk_score", 0),
                    "score_display": score_display,
                    "percentage": competency.get("percentage") if competency else 0,
                    "score": competency.get("score") if competency else 0,
                    "max_score": competency.get("max_score") if competency else 0,
                    "pass_threshold": competency.get("pass_threshold") if competency else 0,
                    "summary": cls._join_evidence_sentences(risk.get("evidence") or []) or "No supporting evidence recorded.",
                }
            )
        return items

    @classmethod
    def _build_consent_summary(cls, session):
        agreement = session.candidate_consent_agreement
        candidate_consented = bool(agreement and agreement.status == "SIGNED")
        return {
            "candidate_consented": candidate_consented,
            "employer_status": "Completed" if candidate_consented else "Not Completed",
            "consent_timestamp": agreement.accepted_at.isoformat() if agreement and agreement.accepted_at else None,
            "consent_version": agreement.version if agreement else None,
        }

    @classmethod
    def _build_identity_verification_summary(cls, session):
        candidate = session.candidate
        captured_artifact = session.artifacts.filter(
            artifact_type__in=["WEBCAM_FRAME", "SELFIE_IMAGE"],
        ).order_by("-uploaded_at").first()
        reference_artifact = session.artifacts.filter(
            artifact_type="ID_DOCUMENT",
        ).order_by("-uploaded_at").first()

        timestamp_source = captured_artifact or reference_artifact
        timestamp = timestamp_source.uploaded_at.isoformat() if timestamp_source is not None else None
        verification_status = session.verification_status or "NOT_STARTED"
        employer_completed = bool(session.identity_verified)
        return {
            "status": "VERIFIED" if session.identity_verified else "NOT_APPLICABLE",
            "method": "SESSION_IDENTITY_VERIFICATION" if session.identity_verified else None,
            "timestamp": timestamp,
            "verification_status": verification_status,
            "verification_label": cls._verification_status_label(verification_status),
            "employer_completed": employer_completed,
            "employer_status": "Completed" if employer_completed else "Not Completed",
            "face_match_score": cls._decimal(session.face_match_score),
            "single_face_detected": bool(session.single_face_detected),
            "liveness_passed": cls._resolve_liveness_passed(session),
            "verification_duration_seconds": cls._derive_verification_duration_seconds(
                session,
                captured_artifact=captured_artifact,
                reference_artifact=reference_artifact,
            ),
            "captured_image_data_uri": (
                cls._file_to_data_uri(getattr(captured_artifact, "file", None))
                or cls._file_to_data_uri(getattr(candidate, "verification_photo", None))
                or cls._file_to_data_uri(getattr(candidate, "profile_photo", None))
                or cls._document_to_image_data_uri(getattr(candidate, "passport_document", None))
            ),
            "verified_photo_data_uri": (
                cls._document_to_image_data_uri(getattr(reference_artifact, "file", None))
                or cls._file_to_data_uri(getattr(candidate, "verification_photo", None))
                or cls._document_to_image_data_uri(getattr(candidate, "passport_document", None))
                or cls._file_to_data_uri(getattr(candidate, "profile_photo", None))
            ),
        }

    @classmethod
    def _build_evaluation_bands(cls, *, competency_breakdown, response_evidence_summary, overall_percentage):
        configs = [
            {
                "code": "COGNITIVE",
                "label": "Cognitive Skills",
                "description": "Problem Solving, Logic, Comprehension",
            },
            {
                "code": "BEHAVIORAL",
                "label": "Behavioral Traits",
                "description": "Adaptability, Integrity, Attitude",
            },
            {
                "code": "TASK_EXECUTION",
                "label": "Task Execution",
                "description": "Accuracy, Efficiency, Consistency",
            },
        ]
        grouped_scores = {config["code"]: [] for config in configs}

        for item in response_evidence_summary:
            grouped_scores[cls._resolve_evaluation_band(item)].append(float(item.get("percentage") or 0))

        fallback_percentage = float(overall_percentage or 0)
        fallback_by_band = {
            "COGNITIVE": cls._fallback_band_percentages(
                competency_breakdown=competency_breakdown,
                keywords=["communication", "language", "knowledge", "problem", "logic", "comprehension"],
            ),
            "BEHAVIORAL": cls._fallback_band_percentages(
                competency_breakdown=competency_breakdown,
                keywords=["behavior", "integrity", "reliability", "professional", "attitude", "patience", "teamwork"],
            ),
            "TASK_EXECUTION": cls._fallback_band_percentages(
                competency_breakdown=competency_breakdown,
                keywords=["task", "safety", "clean", "maintenance", "execution", "operation", "first aid"],
            ),
        }

        results = []
        for config in configs:
            percentages = grouped_scores[config["code"]] or fallback_by_band[config["code"]] or [fallback_percentage]
            score = int(round(sum(percentages) / len(percentages))) if percentages else int(round(fallback_percentage))
            results.append(
                {
                    **config,
                    "score": max(0, min(100, score)),
                    "out_of": 100,
                }
            )
        return results

    @classmethod
    def _fallback_band_percentages(cls, *, competency_breakdown, keywords):
        matches = []
        for item in competency_breakdown:
            haystack = " ".join(
                [
                    str(item.get("competency_code") or "").lower(),
                    str(item.get("competency_name") or "").lower(),
                ]
            )
            if any(keyword in haystack for keyword in keywords):
                matches.append(float(item.get("percentage") or 0))
        return matches

    @classmethod
    def _resolve_evaluation_band(cls, item):
        question_type = str(item.get("question_type") or "").strip().lower()
        competency_text = " ".join(
            [
                str(item.get("competency_code") or "").lower(),
                str(item.get("competency_name") or "").lower(),
            ]
        )

        if question_type in {"behavioral", "integrity"}:
            return "BEHAVIORAL"
        if question_type in {"safety", "task"}:
            return "TASK_EXECUTION"
        if question_type in {"communication", "knowledge", "situational", "scenario"}:
            return "COGNITIVE"

        if any(token in competency_text for token in ["behavior", "integrity", "reliability", "professional", "teamwork"]):
            return "BEHAVIORAL"
        if any(token in competency_text for token in ["task", "safety", "clean", "maintenance", "first aid"]):
            return "TASK_EXECUTION"
        return "COGNITIVE"

    @classmethod
    def _build_role_fit_summary(cls, *, evaluation, overall_percentage, evaluation_bands):
        band_scores = {
            item["code"]: float(item.get("score") or 0)
            for item in evaluation_bands
        }
        role_profiles = [
            {
                "code": "HK",
                "label": "Cleaning",
                "description": "Evaluates hygiene awareness, attention to detail, and task efficiency.",
                "weights": {"TASK_EXECUTION": 0.55, "BEHAVIORAL": 0.20, "COGNITIVE": 0.25},
            },
            {
                "code": "NA",
                "label": "Child Care",
                "description": "Assesses empathy, patience, responsibility, and safety awareness.",
                "weights": {"BEHAVIORAL": 0.45, "TASK_EXECUTION": 0.25, "COGNITIVE": 0.30},
            },
            {
                "code": "EC",
                "label": "Elderly Care",
                "description": "Measures compassion, reliability, communication, and personal care skills.",
                "weights": {"BEHAVIORAL": 0.50, "TASK_EXECUTION": 0.30, "COGNITIVE": 0.20},
            },
            {
                "code": "DR",
                "label": "Driver",
                "description": "Balances hazard awareness, consistency, and route decision-making.",
                "weights": {"TASK_EXECUTION": 0.45, "COGNITIVE": 0.35, "BEHAVIORAL": 0.20},
            },
            {
                "code": "KA",
                "label": "Kitchen Support",
                "description": "Highlights food safety, pacing, and team coordination.",
                "weights": {"TASK_EXECUTION": 0.50, "COGNITIVE": 0.20, "BEHAVIORAL": 0.30},
            },
            {
                "code": "MW",
                "label": "Maintenance",
                "description": "Measures task accuracy, troubleshooting, and dependable follow-through.",
                "weights": {"TASK_EXECUTION": 0.55, "COGNITIVE": 0.20, "BEHAVIORAL": 0.25},
            },
            {
                "code": "OT",
                "label": "General Support",
                "description": "General multi-signal fit across task execution, cognitive, and behavioral traits.",
                "weights": {"TASK_EXECUTION": 0.34, "COGNITIVE": 0.33, "BEHAVIORAL": 0.33},
            },
        ]

        scored_roles = []
        for profile in role_profiles:
            raw_score = 0
            for band_code, weight in profile["weights"].items():
                raw_score += band_scores.get(band_code, float(overall_percentage or 0)) * weight
            match_score = max(0, min(100, int(round(raw_score))))
            scored_roles.append(
                {
                    "code": profile["code"],
                    "label": profile["label"],
                    "description": profile["description"],
                    "match_score": match_score,
                    "match_label": cls._match_label(match_score),
                }
            )

        preferred_labels = ["Cleaning", "Child Care", "Elderly Care"]
        preferred_roles = [
            item for item in scored_roles
            if item["label"] in preferred_labels
        ]
        if len(preferred_roles) >= 3:
            preferred_roles.sort(key=lambda item: preferred_labels.index(item["label"]))
            return preferred_roles[:3]

        current_role_code = evaluation.candidate_job_role or getattr(evaluation.candidate, "job_role", "") or "OT"
        current_role = next((item for item in scored_roles if item["code"] == current_role_code), None)
        alternates = [item for item in scored_roles if item["code"] != current_role_code]
        alternates.sort(key=lambda item: item["match_score"], reverse=True)

        ordered = []
        if current_role is not None:
            ordered.append(current_role)
        ordered.extend(alternates[:2])
        return ordered[:3]

    @classmethod
    def _build_privacy_commitments(cls):
        return [
            {
                "title": "End-to-End Encryption",
                "description": "All assessment data is encrypted in transit and at rest.",
                "retention_days": 30,
            },
            {
                "title": "Purpose Limitation",
                "description": "Candidate data is used strictly for evaluation and hiring workflows.",
                "retention_days": 30,
            },
            {
                "title": "Minimal Data Retention",
                "description": "Only essential interview records are retained for a limited audit window.",
                "retention_days": 30,
            },
            {
                "title": "User Consent & Control",
                "description": "Candidate consent, disclosure, and privacy acknowledgements are captured before assessment.",
                "retention_days": 30,
            },
        ]

    @classmethod
    def _build_compliance_badges(cls):
        return ["GDPR", "ISO/IEC 27001", "SOC 2 Type II"]

    @classmethod
    def _verification_status_label(cls, status):
        labels = {
            "VERIFIED": "Verified",
            "PENDING": "Pending",
            "FAILED": "Failed",
            "NOT_STARTED": "Not Started",
        }
        return labels.get((status or "").upper(), status or "Not Started")

    @classmethod
    def _resolve_liveness_passed(cls, session):
        for log in session.integrity_logs.order_by("-detected_at")[:10]:
            details = log.details or {}
            if details.get("liveness_passed") is not None:
                return bool(details.get("liveness_passed"))
        return True if session.identity_verified else None

    @classmethod
    def _derive_verification_duration_seconds(cls, session, *, captured_artifact, reference_artifact):
        timestamps = [
            artifact.uploaded_at
            for artifact in (captured_artifact, reference_artifact)
            if artifact is not None and artifact.uploaded_at is not None
        ]
        if len(timestamps) < 2:
            return None
        return round((max(timestamps) - min(timestamps)).total_seconds(), 1)

    @classmethod
    def _file_to_data_uri(cls, file_field):
        if not file_field:
            return None
        file_name = getattr(file_field, "name", "") or ""
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        if not mime_type.startswith("image/"):
            return None
        try:
            file_field.open("rb")
            encoded = base64.b64encode(file_field.read()).decode("ascii")
        except Exception:
            return None
        finally:
            try:
                file_field.close()
            except Exception:
                pass
        return f"data:{mime_type};base64,{encoded}"

    @classmethod
    def _document_to_image_data_uri(cls, file_field):
        return cls._file_to_data_uri(file_field) or cls._pdf_file_to_png_data_uri(file_field)

    @classmethod
    def _pdf_file_to_png_data_uri(cls, file_field):
        if not file_field:
            return None

        file_name = getattr(file_field, "name", "") or ""
        if Path(file_name).suffix.lower() != ".pdf":
            return None

        pdftoppm_path = shutil.which("pdftoppm")
        if not pdftoppm_path:
            return None

        try:
            file_field.open("rb")
        except Exception:
            return None

        try:
            with tempfile.TemporaryDirectory(prefix="report-pdf-preview-") as tmpdir:
                source_path = Path(tmpdir) / "reference.pdf"
                output_prefix = str(Path(tmpdir) / "reference-page")

                with open(source_path, "wb") as source_file:
                    if hasattr(file_field, "chunks"):
                        for chunk in file_field.chunks():
                            source_file.write(chunk)
                    else:
                        source_file.write(file_field.read())

                subprocess.run(
                    [
                        pdftoppm_path,
                        "-f",
                        "1",
                        "-l",
                        "1",
                        "-singlefile",
                        "-png",
                        str(source_path),
                        output_prefix,
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                )

                image_path = Path(f"{output_prefix}.png")
                if not image_path.exists():
                    return None

                encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
                return f"data:image/png;base64,{encoded}"
        except Exception:
            return None
        finally:
            try:
                file_field.close()
            except Exception:
                pass

    @classmethod
    def _match_label(cls, score):
        if score >= 88:
            return "Excellent Match"
        if score >= 75:
            return "Strong Match"
        return "Potential Match"

    @classmethod
    def _build_audit_log_stub(cls, *, evaluation, generated_at):
        return {
            "created_by": cls.GENERATED_BY,
            "created_at": generated_at.isoformat(),
            "events": [
                {"event": "REPORT_GENERATED", "timestamp": generated_at.isoformat()},
                {"event": "RULE_ENGINE_DECISION_RECORDED", "timestamp": generated_at.isoformat()},
            ],
            "evaluation_id": str(evaluation.public_id),
        }

    @classmethod
    def _calculate_document_hash(cls, payload):
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _build_qr_verification_url(cls, *, report_number):
        frontend_url = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
        if not frontend_url or "localhost" in frontend_url or "127.0.0.1" in frontend_url:
            frontend_url = cls.PUBLIC_VERIFY_FRONTEND_URL
        return f"{frontend_url}/en/verify-report?id={report_number}"

    @classmethod
    def _derive_qr_verification_status(cls, report_status):
        if report_status == EvaluationReport.STATUS_SUPERSEDED:
            return "Superseded"
        if report_status == EvaluationReport.STATUS_REVOKED:
            return "Revoked"
        return "Authentic"

    @classmethod
    def _derive_role_profile_version(cls, session):
        role_code = (session.role_code or "ROLE").upper().replace(" ", "-")
        version = session.question_set_version or "1.0"
        return f"{role_code}-{version}"

    @classmethod
    def _build_candidate_reference(cls, candidate):
        token = str(candidate.public_id).split("-")[0].upper()
        year = timezone.now().year
        return f"ML-REF-{year}-{token}"

    @classmethod
    def _build_candidate_snapshot(cls, candidate, session):
        return {
            "full_name": candidate.get_full_name(),
            "email": candidate.email,
            "passport_id": candidate.passport_id,
            "role_name": session.role_name or candidate.get_job_role_display(),
            "preferred_language": cls._display_language(candidate.preferred_language),
        }

    @classmethod
    def _display_language(cls, value):
        mapping = {
            "EN": "English",
            "AR": "Arabic",
            "FR": "French",
            "ES": "Spanish",
            "DE": "German",
            "ZH": "Chinese",
        }
        return mapping.get((value or "").upper(), value or "English")

    @classmethod
    def _derive_assessment_duration_minutes(cls, session):
        if session.started_at and session.ended_at:
            return max(1, int((session.ended_at - session.started_at).total_seconds() // 60))
        if session.package_session_config and session.package_session_config.duration_minutes:
            return session.package_session_config.duration_minutes
        return session.config.duration_minutes

    @classmethod
    def _derive_assessment_completeness(cls, summary):
        if not summary.total_response_count:
            return 0
        return int(round((summary.evaluated_response_count / summary.total_response_count) * 100))

    @classmethod
    def _derive_overall_score_display(cls, *, overall_percentage, assessment_completeness):
        if assessment_completeness < 100:
            return "Not fully available"
        return str(cls._rounded_whole(overall_percentage))

    @classmethod
    def _derive_assessment_coverage(cls, session):
        coverage = ["Safety", "Hygiene", "Communication", "Behavioral Indicators"]
        if session.task_observation_enabled:
            coverage.insert(3, "Practical Tasks")
        else:
            coverage.insert(3, "Practical Tasks")
        return coverage

    @classmethod
    def _rounded_whole(cls, value):
        if value in (None, ""):
            return 0
        return int(round(float(value)))

    @classmethod
    def _render_employer_pdf(cls, *, report_payload, report_number):
        employer_payload = cls._sanitize_employer_payload(report_payload)
        html = render_to_string(
            "reports/employer_report.html",
            {
                "report": employer_payload,
                "report_number": report_number,
                "qr_data_uri": cls._build_qr_data_uri(employer_payload.get("qr_verification_url", "")),
                "logo_data_uri": cls._logo_data_uri(),
            },
        )

        if HTML is not None:
            try:
                pdf_bytes = HTML(
                    string=html,
                    base_url=str(getattr(settings, "BASE_DIR", "")),
                ).write_pdf()
                pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
                return pdf_bytes, pdf_hash
            except Exception:
                pass

        playwright_pdf = cls._render_employer_pdf_via_playwright(html=html)
        if playwright_pdf is not None:
            pdf_hash = hashlib.sha256(playwright_pdf).hexdigest()
            return playwright_pdf, pdf_hash

        chrome_pdf = cls._render_employer_pdf_via_chrome(html=html)
        if chrome_pdf is not None:
            pdf_hash = hashlib.sha256(chrome_pdf).hexdigest()
            return chrome_pdf, pdf_hash

        pdf_lines = cls._build_employer_pdf_lines(
            report_payload=employer_payload,
            report_number=report_number,
        )
        pdf_bytes = cls._build_simple_pdf(pdf_lines)
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
        return pdf_bytes, pdf_hash

    @classmethod
    def _logo_data_uri(cls):
        if cls._logo_data_uri_cache is None:
            try:
                encoded = base64.b64encode(cls.LOGO_PATH.read_bytes()).decode("ascii")
                cls._logo_data_uri_cache = f"data:image/png;base64,{encoded}"
            except Exception:
                cls._logo_data_uri_cache = None
        return cls._logo_data_uri_cache

    @classmethod
    def _render_employer_pdf_via_playwright(cls, *, html):
        playwright_binary = shutil.which("playwright")
        if not playwright_binary:
            return None

        with tempfile.TemporaryDirectory(prefix="meritlense-report-pw-") as temp_dir:
            temp_path = Path(temp_dir)
            html_path = temp_path / "employer-report.html"
            pdf_path = temp_path / "employer-report.pdf"
            html_path.write_text(html, encoding="utf-8")

            command_sets = [
                [
                    playwright_binary,
                    "pdf",
                    "--channel",
                    "chrome",
                    "--paper-format",
                    "A4",
                    "--wait-for-timeout",
                    "1200",
                    html_path.resolve().as_uri(),
                    str(pdf_path),
                ],
                [
                    playwright_binary,
                    "pdf",
                    "--paper-format",
                    "A4",
                    "--wait-for-timeout",
                    "1200",
                    html_path.resolve().as_uri(),
                    str(pdf_path),
                ],
            ]

            for command in command_sets:
                try:
                    subprocess.run(
                        command,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=90,
                    )
                except (OSError, subprocess.SubprocessError):
                    continue

                if pdf_path.exists():
                    return pdf_path.read_bytes()

        return None

    @classmethod
    def _render_employer_pdf_via_chrome(cls, *, html):
        chrome_binary = cls._resolve_chrome_binary()
        if not chrome_binary:
            return None

        with tempfile.TemporaryDirectory(prefix="meritlense-report-") as temp_dir:
            temp_path = Path(temp_dir)
            html_path = temp_path / "employer-report.html"
            pdf_path = temp_path / "employer-report.pdf"
            profile_dir = temp_path / "chrome-profile"
            cache_dir = temp_path / "xdg-cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            html_path.write_text(html, encoding="utf-8")
            chrome_env = os.environ.copy()
            chrome_env.setdefault("XDG_CACHE_HOME", str(cache_dir))

            chrome_commands = [
                [
                    chrome_binary,
                    "--headless=new",
                    "--disable-gpu",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--allow-file-access-from-files",
                    "--no-pdf-header-footer",
                    f"--user-data-dir={profile_dir}",
                    f"--print-to-pdf={pdf_path}",
                    "--print-to-pdf-no-header",
                    html_path.resolve().as_uri(),
                ],
                [
                    chrome_binary,
                    "--headless",
                    "--disable-gpu",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--allow-file-access-from-files",
                    "--no-pdf-header-footer",
                    f"--user-data-dir={profile_dir}",
                    f"--print-to-pdf={pdf_path}",
                    "--print-to-pdf-no-header",
                    html_path.resolve().as_uri(),
                ],
            ]

            for command in chrome_commands:
                try:
                    subprocess.run(
                        command,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=90,
                        env=chrome_env,
                    )
                except (OSError, subprocess.SubprocessError):
                    continue

                if pdf_path.exists():
                    return pdf_path.read_bytes()

        return None

    @classmethod
    def _resolve_chrome_binary(cls):
        configured = [
            getattr(settings, "CHROME_BIN", ""),
        ]
        configured.extend(
            [
                os_value
                for os_value in [
                    os.environ.get("GOOGLE_CHROME_BIN"),
                    os.environ.get("CHROME_BIN"),
                ]
                if os_value
            ]
        )

        discovered = [
            shutil.which("google-chrome"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            shutil.which("chrome"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]

        for candidate in configured + discovered:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    @classmethod
    def _build_qr_data_uri(cls, verification_url):
        if not verification_url or qrcode is None:
            return None
        qr_image = qrcode.make(verification_url)
        buffer = BytesIO()
        qr_image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    @classmethod
    def _build_employer_pdf_lines(cls, *, report_payload, report_number):
        context = report_payload.get("assessment_context", {})
        summary = report_payload.get("executive_summary", {})
        readiness = summary.get("readiness_indicator", {})
        methodology = report_payload.get("assessment_methodology", [])
        critical_status = report_payload.get("critical_competency_status", [])
        competency_breakdown = report_payload.get("competency_breakdown", [])
        risk_indicators = report_payload.get("risk_indicators", {})
        evidence_summary = report_payload.get("evidence_summary", [])
        improvement_plan = report_payload.get("improvement_plan", [])
        identity = report_payload.get("identity_verification", {})
        integrity = report_payload.get("document_integrity", {})

        lines = [
            report_payload.get("report_name", "MeritLense Workforce Readiness Assessment Report"),
            report_payload.get("classification", "CONFIDENTIAL - Internal Use Only"),
            f"Report Number: {report_number}",
            f"Report Version: {report_payload.get('report_version', '')}",
            f"Generated At: {report_payload.get('generated_at', '')}",
            f"Generated By: {report_payload.get('generated_by', '')}",
            "",
            "Assessment Context",
            f"Target Role: {context.get('target_role', '')}",
            f"Assessment Date: {context.get('assessment_date', '')}",
            f"Assessment Status: {context.get('assessment_status', '')}",
            f"Assessment Duration Minutes: {context.get('assessment_duration_minutes', '')}",
            f"Assessment Quality: {context.get('assessment_quality', '')}",
            f"Candidate Reference: {context.get('candidate_reference', '')}",
            f"Assessment Language: {context.get('assessment_language', '')}",
            "",
            "Executive Summary",
            f"Readiness Indicator: {readiness.get('display', readiness.get('value', ''))}",
            f"Readiness Reason: {(summary.get('readiness_reason') or {}).get('employer_message', '')}",
            f"Overall Score: {summary.get('overall_score_display', summary.get('overall_score', ''))}"
            f"{'%' if summary.get('overall_score_available', True) else ''}",
            f"Assessment Scope: {summary.get('assessment_scope', '')}",
            f"Suggested Action: {summary.get('suggested_action_display', '')}",
            f"Assessment Coverage: {', '.join(context.get('assessment_coverage', []))}",
            f"Evaluation Reliability: {summary.get('evaluation_reliability', '')}",
        ]
        for factor in summary.get("reliability_factors", []):
            lines.append(f"  - {factor}")
        lines.extend(["", "Top Strengths"])
        for strength in summary.get("top_strengths", []):
            lines.append(f"- {strength}")
        lines.extend(["", "Top Risks"])
        for risk in summary.get("top_risks", []):
            lines.append(f"- {risk}")
        lines.extend(["", "Assessment Methodology"])
        for item in methodology:
            lines.extend(
                [
                    f"- {item.get('domain', '')}",
                    f"  Evaluated: {item.get('evaluated', '')}",
                    f"  Method: {item.get('method', '')}",
                ]
            )
        lines.extend(["", "Critical Competency Status"])
        for item in critical_status:
            lines.append(
                f"- {item.get('label', '')}: {item.get('status_label', '')} | "
                f"{item.get('score_display', '')}"
            )
        lines.extend(["", "Competency Breakdown"])
        for item in competency_breakdown:
            lines.extend(
                [
                    f"- {item.get('display_name') or item.get('competency_name') or item.get('competency_code', '')}",
                    (
                        "  Score: "
                        f"{item.get('score_display', '')} | "
                        f"Status: {item.get('assessment_status', item.get('status', ''))}"
                    ),
                ]
            )
        lines.extend([
            "",
            "Risk Indicators",
        ])

        if risk_indicators:
            for risk_name, risk in risk_indicators.items():
                if risk_name == "note":
                    continue
                if not isinstance(risk, dict):
                    lines.append(f"- {risk_name}: {risk}")
                    continue
                title = risk_name.replace("_", " ").replace("risk", "risk").title()
                detail = cls._join_evidence_sentences(risk.get("evidence") or []) or "No supporting evidence recorded."
                lines.extend(
                    [
                        f"- {title}: {risk.get('level', '')} ({risk.get('risk_score_display', risk.get('risk_score', ''))})",
                        f"  Detail: {detail}",
                    ]
                )
        else:
            lines.append("- No material risk indicators were recorded.")

        lines.extend(["", "Evidence Summary"])
        if evidence_summary:
            for item in evidence_summary:
                lines.extend(
                    [
                        f"- {item.get('category', 'Evidence')}: {item.get('finding', '')}",
                        f"  Source: {item.get('source', '')} | Severity: {item.get('severity', '')}",
                    ]
                )
        else:
            lines.append("- No evidence summary available.")

        lines.extend(["", "Improvement Plan"])
        if improvement_plan:
            for item in improvement_plan:
                lines.extend(
                    [
                        f"- {item.get('priority', 'Priority')} | {item.get('competency', '')}",
                        f"  Gap: {item.get('gap', '')}",
                        f"  Recommendation: {item.get('recommended_action', '')}",
                    ]
                )
        else:
            lines.append("- No improvement actions were recommended.")

        lines.extend(
            [
                "",
                "Verification",
                f"Identity Verification Status: {identity.get('employer_status', '')}",
                f"Verification URL: {report_payload.get('qr_verification_url', '')}",
                f"Document Hash: {integrity.get('hash_value', '')}",
                "",
                "Legal Disclaimer",
                report_payload.get("legal_disclaimer", ""),
            ]
        )
        return lines

    @classmethod
    def _build_simple_pdf(cls, lines):
        page_width = 595
        page_height = 842
        left_margin = 50
        top_start = 790
        line_height = 16
        body_lines_per_page = 42

        total_pages = max(1, (len(lines) + body_lines_per_page - 1) // body_lines_per_page)
        pages = []
        for index in range(total_pages):
            page_lines = lines[index * body_lines_per_page : (index + 1) * body_lines_per_page]
            page_lines = list(page_lines)
            page_lines.extend(
                [
                    "",
                    f"Page {index + 1} of {total_pages}",
                    "MeritLense Workforce Readiness Assessment Report",
                ]
            )
            pages.append(page_lines)

        objects = []
        page_object_numbers = []
        content_object_numbers = []
        next_object_number = 4
        for _ in pages:
            page_object_numbers.append(next_object_number)
            content_object_numbers.append(next_object_number + 1)
            next_object_number += 2

        font_object_number = 3
        kids = " ".join(f"{obj_num} 0 R" for obj_num in page_object_numbers)
        pages_object = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_object_numbers)} >>".encode("latin-1")
        catalog_object = b"<< /Type /Catalog /Pages 2 0 R >>"
        font_object = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

        objects.extend([catalog_object, pages_object, font_object])

        for page_object_number, content_object_number, page_lines in zip(
            page_object_numbers,
            content_object_numbers,
            pages,
        ):
            content_stream = cls._build_pdf_content_stream(
                page_lines=page_lines,
                left_margin=left_margin,
                top_start=top_start,
                line_height=line_height,
            )
            page_object = (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
                f"/Resources << /Font << /F1 {font_object_number} 0 R >> >> "
                f"/Contents {content_object_number} 0 R >>"
            ).encode("latin-1")
            content_object = (
                f"<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1")
                + content_stream
                + b"\nendstream"
            )
            objects.extend([page_object, content_object])

        buffer = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(buffer))
            buffer.extend(f"{index} 0 obj\n".encode("latin-1"))
            buffer.extend(obj)
            buffer.extend(b"\nendobj\n")

        xref_start = len(buffer)
        buffer.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
        buffer.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            buffer.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
        buffer.extend(
            (
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{xref_start}\n%%EOF"
            ).encode("latin-1")
        )
        return bytes(buffer)

    @classmethod
    def _build_pdf_content_stream(cls, *, page_lines, left_margin, top_start, line_height):
        escaped_lines = [cls._escape_pdf_text(line) for line in page_lines]
        commands = [
            "BT",
            "/F1 11 Tf",
            f"{left_margin} {top_start} Td",
            f"{line_height} TL",
        ]
        for idx, line in enumerate(escaped_lines):
            if idx == 0:
                commands.append(f"({line}) Tj")
            else:
                commands.append(f"T* ({line}) Tj")
        commands.append("ET")
        return "\n".join(commands).encode("latin-1")

    @classmethod
    def _escape_pdf_text(cls, value):
        text = (value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return text.encode("latin-1", "replace").decode("latin-1")

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

    @staticmethod
    def _get_rule_engine_version(readiness_record):
        if readiness_record is None:
            return ""
        return readiness_record.rule_engine_version

    @staticmethod
    def _get_override_triggered(evaluation, readiness_record):
        if readiness_record is not None:
            return readiness_record.override_triggered
        return bool(evaluation.readiness_override_applied)

    @staticmethod
    def _get_readiness_reason(evaluation, readiness_record):
        if readiness_record is not None:
            return readiness_record.readiness_reason
        return evaluation.readiness_override_reason or ""
