import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation
from html import unescape

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity
from api.interviews.models import InterviewRubric
from api.sessions.models import CandidateResponse
from api.storage.services import enqueue_background_job

from .models import (
    AIProcessingJob,
    CandidateResponseInterpretation,
    CandidateResponseTranslation,
    EvaluationInputArtifact,
)


class AIProcessingError(Exception):
    def __init__(self, message, *, code="ai_processing_error", metadata=None):
        super().__init__(message)
        self.code = code
        self.metadata = metadata or {}


class AIProcessingConfigurationError(AIProcessingError):
    pass


class UnsupportedAIOutputError(AIProcessingError):
    pass


LANGUAGE_NORMALIZATION_MAP = {
    "EN": "en",
    "EN-US": "en",
    "EN-GB": "en",
    "ENGLISH": "en",
    "ES": "es",
    "ES-ES": "es",
    "SPANISH": "es",
    "FR": "fr",
    "FR-FR": "fr",
    "FRENCH": "fr",
    "AR": "ar",
    "AR-SA": "ar",
    "ARABIC": "ar",
    "DE": "de",
    "DE-DE": "de",
    "GERMAN": "de",
    "ZH": "zh",
    "ZH-CN": "zh",
    "CHINESE": "zh",
}

PROHIBITED_INTERPRETATION_FIELDS = {
    "score",
    "question_score",
    "normalized_score",
    "ai_score",
    "final_score",
    "final_decision",
    "pass",
    "fail",
    "suitable",
    "not_suitable",
    "hire",
    "reject",
    "recommendation",
    "ranking",
}

LEGAL_DISCLAIMER_TEXT = (
    "AI outputs are advisory extraction artifacts only and never constitute employment decisions."
)


def normalize_language_code(value):
    normalized = (value or "").strip().upper()
    if not normalized:
        return ""
    if normalized in LANGUAGE_NORMALIZATION_MAP:
        return LANGUAGE_NORMALIZATION_MAP[normalized]
    if "-" in normalized:
        return normalized.split("-", 1)[0].lower()
    return normalized.lower()


def sanitize_list(value):
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            items.append(text)
    return items


def parse_confidence_score(value):
    if value in (None, ""):
        return None
    try:
        score = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if score < Decimal("0"):
        score = Decimal("0")
    if score > Decimal("1"):
        score = Decimal("1")
    return score.quantize(Decimal("0.001"))


class GoogleTranslationProvider:
    def __init__(self):
        self.provider = settings.TRANSLATION_PROVIDER
        self.api_url = settings.TRANSLATION_API_URL
        self.api_key = settings.GOOGLE_TRANSLATE_API_KEY
        self.timeout_seconds = settings.TRANSLATION_TIMEOUT_SECONDS
        self.project_id = settings.GOOGLE_TRANSLATE_PROJECT_ID

    @property
    def is_configured(self):
        return bool(self.api_url and self.api_key)

    def translate(self, *, text, source_language="", target_language="en"):
        if not self.is_configured:
            raise AIProcessingConfigurationError(
                "Translation provider is not configured",
                code="translation_not_configured",
            )

        payload = {
            "q": text,
            "target": target_language,
            "format": "text",
        }
        if source_language:
            payload["source"] = source_language

        params = {"key": self.api_key}
        if self.project_id:
            params["project"] = self.project_id

        try:
            response = requests.post(
                self.api_url,
                params=params,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise AIProcessingError(
                "Translation provider timed out",
                code="translation_timeout",
            ) from exc
        except requests.RequestException as exc:
            status_code = getattr(exc.response, "status_code", None)
            raise AIProcessingError(
                "Translation provider request failed",
                code="translation_request_failed",
                metadata={"status_code": status_code},
            ) from exc

        body = response.json()
        translations = (((body or {}).get("data") or {}).get("translations") or [])
        if not translations:
            raise AIProcessingError(
                "Translation provider returned no translation",
                code="translation_empty_response",
            )
        translation = translations[0]
        translated_text = unescape((translation.get("translatedText") or "").strip())
        if not translated_text:
            raise AIProcessingError(
                "Translation provider returned an empty translation",
                code="translation_empty_text",
            )
        return {
            "provider": self.provider,
            "provider_model": translation.get("model") or "google-translate-v2",
            "translated_text": translated_text,
            "source_language": translation.get("detectedSourceLanguage") or source_language,
            "target_language": target_language,
            "metadata": {
                "request_id": response.headers.get("x-request-id", ""),
                "project_id_present": bool(self.project_id),
            },
        }


class OpenAIInterpretationProvider:
    def __init__(self):
        self.provider = "OPENAI"
        self.api_url = settings.OPENAI_INTERPRETATION_API_URL
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.OPENAI_INTERPRETATION_MODEL
        self.timeout_seconds = settings.AI_INTERPRETATION_TIMEOUT_SECONDS

    @property
    def is_configured(self):
        return bool(self.api_url and self.api_key and self.model)

    def interpret(self, *, prompt):
        if not self.is_configured:
            raise AIProcessingConfigurationError(
                "OpenAI interpretation provider is not configured",
                code="interpretation_not_configured",
            )

        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "You extract structured interview signals only and respond with valid JSON.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise AIProcessingError(
                "Interpretation provider timed out",
                code="interpretation_timeout",
            ) from exc
        except requests.RequestException as exc:
            status_code = getattr(exc.response, "status_code", None)
            raise AIProcessingError(
                "Interpretation provider request failed",
                code="interpretation_request_failed",
                metadata={"status_code": status_code},
            ) from exc

        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise AIProcessingError(
                "Interpretation provider returned no choices",
                code="interpretation_empty_response",
            )
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content") or "{}"
        return {
            "provider": self.provider,
            "model": body.get("model") or self.model,
            "raw_content": content,
            "metadata": {
                "request_id": response.headers.get("x-request-id", ""),
                "finish_reason": (choices[0] or {}).get("finish_reason"),
                "usage": body.get("usage") or {},
            },
        }


class GeminiInterpretationProvider:
    def __init__(self):
        self.provider = "GEMINI"
        self.api_url = settings.GEMINI_INTERPRETATION_API_URL
        self.api_key = settings.GEMINI_API_KEY
        self.model = settings.GEMINI_INTERPRETATION_MODEL
        self.timeout_seconds = settings.AI_INTERPRETATION_TIMEOUT_SECONDS

    @property
    def is_configured(self):
        return bool(self.api_url and self.api_key and self.model)

    def interpret(self, *, prompt):
        if not self.is_configured:
            raise AIProcessingConfigurationError(
                "Gemini interpretation provider is not configured",
                code="interpretation_not_configured",
            )

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt,
                        }
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
            },
        }

        try:
            response = requests.post(
                self.api_url.format(model=self.model),
                params={"key": self.api_key},
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise AIProcessingError(
                "Interpretation provider timed out",
                code="interpretation_timeout",
            ) from exc
        except requests.RequestException as exc:
            status_code = getattr(exc.response, "status_code", None)
            raise AIProcessingError(
                "Interpretation provider request failed",
                code="interpretation_request_failed",
                metadata={"status_code": status_code},
            ) from exc

        body = response.json()
        candidates = body.get("candidates") or []
        if not candidates:
            raise AIProcessingError(
                "Interpretation provider returned no candidates",
                code="interpretation_empty_response",
            )
        content = ((((candidates[0] or {}).get("content") or {}).get("parts") or [{}])[0]).get("text") or "{}"
        return {
            "provider": self.provider,
            "model": self.model,
            "raw_content": content,
            "metadata": {
                "usage": body.get("usageMetadata") or {},
                "finish_reason": (candidates[0] or {}).get("finishReason"),
            },
        }


class TranslationService:
    provider_class = GoogleTranslationProvider

    @classmethod
    def translate(cls, *, text, source_language, target_language):
        provider = cls.provider_class()
        return provider.translate(
            text=text,
            source_language=source_language,
            target_language=target_language,
        )


class ResponseInterpretationService:
    prompt_version = settings.AI_INTERPRETATION_PROMPT_VERSION

    @classmethod
    def get_provider(cls):
        provider = settings.AI_INTERPRETATION_PROVIDER
        if provider == "GEMINI":
            return GeminiInterpretationProvider()
        return OpenAIInterpretationProvider()

    @classmethod
    def build_prompt(cls, *, response, transcript, input_language, input_transcript_type):
        template = getattr(response.question, "question_template", None)
        rubric = None
        if template and template.skill_tag:
            rubric = (
                InterviewRubric.objects.filter(
                    role_code__iexact=response.session.role_code,
                    skill_tag__iexact=template.skill_tag,
                )
                .order_by("-created_at")
                .first()
            )

        expected_steps = template.expected_steps if template else []
        keywords = template.keywords if template else []
        rubric_notes = rubric.evaluation_criteria if rubric else []
        prompt_payload = {
            "prompt_version": cls.prompt_version,
            "instruction": (
                "Extract structured interview signals only. "
                "Return strict JSON with these keys only when supported by the transcript: "
                "answer_relevance, mentioned_steps, missing_steps, safety_risks, compliance_risks, "
                "language_quality, extraction_confidence, confidence_notes, uncertainty_notes, transcript_issues, "
                "key_evidence_phrases. "
                "Do not return any scoring, pass/fail, suitability, hiring, ranking, or recommendation fields."
            ),
            "legal_disclaimer": LEGAL_DISCLAIMER_TEXT,
            "session": {
                "response_id": str(response.public_id),
                "session_id": str(response.session.public_id),
                "question_id": str(response.question.public_id),
                "input_language": input_language,
                "input_transcript_type": input_transcript_type,
            },
            "question": {
                "text": response.question.question_text,
                "skill": response.question.skill,
                "domain": response.question.domain,
                "expected_steps": expected_steps,
                "keywords": keywords,
                "rubric_notes": rubric_notes,
            },
            "transcript": transcript,
        }
        return json.dumps(prompt_payload, ensure_ascii=True)

    @classmethod
    def build_prompt_hash(cls, prompt):
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    @classmethod
    def parse_and_validate(cls, raw_content):
        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise AIProcessingError(
                "Interpretation provider returned invalid JSON",
                code="interpretation_invalid_json",
            ) from exc

        if not isinstance(payload, dict):
            raise AIProcessingError(
                "Interpretation provider returned a non-object payload",
                code="interpretation_invalid_schema",
            )

        lowered_keys = {key.lower() for key in payload.keys()}
        prohibited = sorted(lowered_keys.intersection(PROHIBITED_INTERPRETATION_FIELDS))
        if prohibited:
            raise UnsupportedAIOutputError(
                "Interpretation payload included prohibited scoring or hiring fields",
                code="interpretation_policy_validation_failed",
                metadata={"prohibited_fields": prohibited},
            )

        normalized = {
            "answer_relevance": str(payload.get("answer_relevance", "")).strip().lower(),
            "mentioned_steps": sanitize_list(payload.get("mentioned_steps")),
            "missing_steps": sanitize_list(payload.get("missing_steps")),
            "safety_risks": sanitize_list(payload.get("safety_risks")),
            "compliance_risks": sanitize_list(payload.get("compliance_risks")),
            "language_quality": str(payload.get("language_quality", "")).strip().lower(),
            "extraction_confidence": parse_confidence_score(payload.get("extraction_confidence")),
            "confidence_notes": sanitize_list(payload.get("confidence_notes")),
            "uncertainty_notes": sanitize_list(payload.get("uncertainty_notes")),
            "transcript_issues": sanitize_list(payload.get("transcript_issues")),
            "key_evidence_phrases": sanitize_list(payload.get("key_evidence_phrases")),
        }
        normalized["risk_flags"] = normalized["safety_risks"] + normalized["compliance_risks"]
        return payload, normalized

    @classmethod
    def interpret(cls, *, response, transcript, input_language, input_transcript_type):
        provider = cls.get_provider()
        prompt = cls.build_prompt(
            response=response,
            transcript=transcript,
            input_language=input_language,
            input_transcript_type=input_transcript_type,
        )
        prompt_hash = cls.build_prompt_hash(prompt)
        result = provider.interpret(prompt=prompt)
        structured_output, normalized = cls.parse_and_validate(result["raw_content"])
        confidence_score = normalized.get("extraction_confidence")
        storage_normalized = {
            **normalized,
            "extraction_confidence": str(confidence_score) if confidence_score is not None else None,
        }
        result["prompt_version"] = cls.prompt_version
        result["prompt_hash"] = prompt_hash
        result["structured_output"] = structured_output
        result["normalized"] = storage_normalized
        result["confidence_score"] = confidence_score
        return result


class EvaluationInputBuilderService:
    @classmethod
    def build(cls, *, response, interpretation):
        template = getattr(response.question, "question_template", None)
        expected_indicators = template.expected_steps if template else []
        observed_indicators = interpretation.normalized_indicators.get("mentioned_steps") or []
        missing_indicators = interpretation.normalized_indicators.get("missing_steps") or []
        competency_code = ""
        if template:
            competency_code = template.skill_id or template.skill_tag or template.skill
        language_notes = {
            "clarity": interpretation.normalized_indicators.get("language_quality", ""),
            "translation_used": bool(response.translated_transcript and response.translation_status == "COMPLETED"),
            "input_language": interpretation.input_language,
        }
        review_reasons = []
        confidence_score = interpretation.confidence_score
        if confidence_score is not None and confidence_score < Decimal(str(settings.AI_INTERPRETATION_MIN_CONFIDENCE)):
            review_reasons.append(
                f"Interpretation confidence {confidence_score} is below the configured threshold "
                f"{settings.AI_INTERPRETATION_MIN_CONFIDENCE}."
            )
        if interpretation.normalized_indicators.get("uncertainty_notes"):
            review_reasons.append("Interpretation returned uncertainty notes that need human review.")
        if interpretation.normalized_indicators.get("transcript_issues"):
            review_reasons.append("Transcript issues were detected and should be reviewed by a human.")
        return {
            "competency_code": competency_code,
            "expected_indicators": expected_indicators,
            "observed_indicators": observed_indicators,
            "missing_indicators": missing_indicators,
            "risk_flags": interpretation.risk_flags,
            "language_notes": language_notes,
            "requires_human_review": bool(review_reasons),
            "review_reason": " ".join(review_reasons).strip(),
            "legal_disclaimer": LEGAL_DISCLAIMER_TEXT,
            "metadata": {
                "question_code": getattr(template, "question_code", ""),
                "question_version": getattr(template, "question_version", ""),
                "rubric_version": response.session.rubric_version,
                "prompt_version": interpretation.prompt_version,
                "prompt_hash": interpretation.prompt_hash,
                "confidence_score": str(confidence_score) if confidence_score is not None else None,
                "legal_disclaimer": LEGAL_DISCLAIMER_TEXT,
            },
        }


class AIProcessingOrchestrationService:
    @classmethod
    def _default_idempotency_key(cls, *, response, async_execution=False):
        prefix = "async" if async_execution else "sync"
        return f"{prefix}:{response.public_id}"

    @classmethod
    def _resolve_idempotency_key(cls, *, response, idempotency_key="", async_execution=False):
        key = (idempotency_key or "").strip()
        if key:
            return key
        return cls._default_idempotency_key(response=response, async_execution=async_execution)

    @classmethod
    def _log_event(
        cls,
        *,
        actor,
        action,
        description,
        response,
        severity=AuditLogSeverity.INFO,
        extra=None,
    ):
        data = {
            "session_id": str(response.session.public_id),
            "question_id": str(response.question.public_id),
            "candidate_response_id": str(response.public_id),
            "source_language": response.translation_source_language or response.transcript_language,
            "target_language": response.translation_target_language or response.session.translation_target,
            "processing_status": response.processing_status,
        }
        if extra:
            data.update(extra)
        if actor:
            AuditLogService.log(
                user=actor,
                action=action,
                category=AuditLogCategory.AI_PROCESSING,
                description=description,
                resource=response.session,
                severity=severity,
                data=data,
            )
        else:
            AuditLogService.log_system(
                action=action,
                category=AuditLogCategory.AI_PROCESSING,
                description=description,
                resource=response.session,
                severity=severity,
                data=data,
            )

    @classmethod
    def _get_source_transcript(cls, response):
        transcript = (response.original_transcript or response.transcript or "").strip()
        if not transcript:
            raise AIProcessingError(
                "A transcript is required before AI processing can run",
                code="transcript_missing",
            )
        return transcript

    @classmethod
    def _resolve_languages(cls, response, target_override=""):
        source_language = normalize_language_code(
            response.transcript_language or response.session.stt_language_code or response.session.candidate_language
        )
        target_candidate = (
            target_override
            or response.session.translation_target
            or response.session.ui_language
            or settings.DEFAULT_EVALUATION_LANGUAGE
        )
        target_language = normalize_language_code(target_candidate)
        if not source_language:
            raise AIProcessingError(
                "Transcript source language is not supported",
                code="unsupported_source_language",
            )
        if not target_language:
            raise AIProcessingError(
                "Target language is not supported",
                code="unsupported_target_language",
            )
        return source_language, target_language

    @classmethod
    def _get_async_job(cls, response, *, idempotency_key=""):
        queryset = response.ai_processing_jobs.order_by("-created_at")
        if idempotency_key:
            queryset = queryset.filter(idempotency_key=idempotency_key)
        return queryset.first()

    @classmethod
    @transaction.atomic
    def translate_response(cls, *, response, actor=None, force=False, target_override="", idempotency_key=""):
        if not settings.ENABLE_TRANSLATION_PIPELINE:
            response.translation_status = "NOT_ENABLED"
            response.processing_status = "TRANSLATION_COMPLETED"
            response.save(update_fields=["translation_status", "processing_status", "updated_at"])
            return response
        if response.translation_status == "COMPLETED" and not force:
            return response

        transcript = cls._get_source_transcript(response)
        source_language, target_language = cls._resolve_languages(response, target_override=target_override)
        translation_artifact, _ = CandidateResponseTranslation.objects.get_or_create(
            response=response,
            defaults={
                "session": response.session,
                "question": response.question,
            },
        )
        translation_artifact.idempotency_key = idempotency_key

        response.processing_status = "TRANSLATION_PENDING"
        response.translation_status = "PROCESSING"
        response.translation_error = ""
        response.translation_source_language = source_language
        response.translation_target_language = target_language
        response.ai_processing_idempotency_key = idempotency_key
        response.save(
            update_fields=[
                "processing_status",
                "translation_status",
                "translation_error",
                "translation_source_language",
                "translation_target_language",
                "ai_processing_idempotency_key",
                "updated_at",
            ]
        )
        cls._log_event(
            actor=actor,
            action=AuditLogAction.TRANSLATION_REQUESTED,
            description=f"Translation requested for response {response.public_id}",
            response=response,
            extra={"provider": settings.TRANSLATION_PROVIDER},
        )

        if source_language == target_language:
            response.translation_status = "NOT_REQUIRED"
            response.translation_error = ""
            response.translation_provider = ""
            response.translation_model = ""
            response.translation_metadata = {
                "reason": "source_language_matches_target_language",
            }
            response.translated_transcript = ""
            response.translated_at = timezone.now()
            response.processing_status = "TRANSLATION_COMPLETED"
            response.save(
                update_fields=[
                    "translation_status",
                    "translation_error",
                    "translation_provider",
                    "translation_model",
                    "translation_metadata",
                    "translated_transcript",
                    "translated_at",
                    "processing_status",
                    "updated_at",
                ]
            )
            translation_artifact.source_language = source_language
            translation_artifact.target_language = target_language
            translation_artifact.original_transcript = transcript
            translation_artifact.translated_transcript = ""
            translation_artifact.provider = ""
            translation_artifact.provider_model = ""
            translation_artifact.status = "NOT_REQUIRED"
            translation_artifact.error_message = ""
            translation_artifact.metadata = {
                "reason": "source_language_matches_target_language",
                "legal_disclaimer": LEGAL_DISCLAIMER_TEXT,
            }
            translation_artifact.translated_at = response.translated_at
            translation_artifact.save()
            cls._log_event(
                actor=actor,
                action=AuditLogAction.TRANSLATION_COMPLETED,
                description=f"Translation skipped for response {response.public_id}",
                response=response,
                extra={"translation_status": "NOT_REQUIRED"},
            )
            return response

        try:
            result = TranslationService.translate(
                text=transcript,
                source_language=source_language,
                target_language=target_language,
            )
        except AIProcessingError as exc:
            response.translation_status = "FAILED"
            response.translation_error = str(exc)
            response.translation_metadata = {"error_metadata": exc.metadata}
            response.processing_status = "FAILED"
            response.save(
                update_fields=[
                    "translation_status",
                    "translation_error",
                    "translation_metadata",
                    "processing_status",
                    "updated_at",
                ]
            )
            translation_artifact.source_language = source_language
            translation_artifact.target_language = target_language
            translation_artifact.original_transcript = transcript
            translation_artifact.status = "FAILED"
            translation_artifact.error_message = str(exc)
            translation_artifact.metadata = {
                "error_metadata": exc.metadata,
                "legal_disclaimer": LEGAL_DISCLAIMER_TEXT,
            }
            translation_artifact.save()
            cls._log_event(
                actor=actor,
                action=AuditLogAction.TRANSLATION_FAILED,
                description=f"Translation failed for response {response.public_id}",
                response=response,
                severity=AuditLogSeverity.ERROR,
                extra={"error_code": exc.code, "error_metadata": exc.metadata},
            )
            return response

        response.translated_transcript = result["translated_text"]
        response.translation_source_language = result["source_language"]
        response.translation_target_language = result["target_language"]
        response.translation_provider = result["provider"]
        response.translation_model = result["provider_model"]
        response.translation_status = "COMPLETED"
        response.translation_error = ""
        response.translation_metadata = result["metadata"]
        response.translated_at = timezone.now()
        response.processing_status = "TRANSLATION_COMPLETED"
        response.save(
            update_fields=[
                "translated_transcript",
                "translation_source_language",
                "translation_target_language",
                "translation_provider",
                "translation_model",
                "translation_status",
                "translation_error",
                "translation_metadata",
                "translated_at",
                "processing_status",
                "updated_at",
            ]
        )

        translation_artifact.source_language = result["source_language"]
        translation_artifact.target_language = result["target_language"]
        translation_artifact.original_transcript = transcript
        translation_artifact.translated_transcript = result["translated_text"]
        translation_artifact.provider = result["provider"]
        translation_artifact.provider_model = result["provider_model"]
        translation_artifact.status = "COMPLETED"
        translation_artifact.error_message = ""
        translation_artifact.metadata = {
            **result["metadata"],
            "legal_disclaimer": LEGAL_DISCLAIMER_TEXT,
        }
        translation_artifact.translated_at = response.translated_at
        translation_artifact.save()

        cls._log_event(
            actor=actor,
            action=AuditLogAction.TRANSLATION_COMPLETED,
            description=f"Translation completed for response {response.public_id}",
            response=response,
            extra={
                "provider": response.translation_provider,
                "translation_status": response.translation_status,
            },
        )
        return response

    @classmethod
    def _resolve_interpretation_input(cls, response):
        transcript = cls._get_source_transcript(response)
        source_language, target_language = cls._resolve_languages(response)
        translation_required = source_language != target_language
        if translation_required:
            if response.translation_status != "COMPLETED":
                raise AIProcessingError(
                    "Translation must complete before interpretation when languages differ",
                    code="translation_required_before_interpretation",
                )
            return response.translated_transcript, target_language, "translated"
        return transcript, source_language, "original"

    @classmethod
    @transaction.atomic
    def interpret_response(cls, *, response, actor=None, force=False, idempotency_key=""):
        if not settings.ENABLE_RESPONSE_INTERPRETATION:
            raise AIProcessingConfigurationError(
                "Response interpretation is disabled",
                code="interpretation_disabled",
            )
        if response.interpretation_status == "COMPLETED" and not force:
            return response

        input_transcript, input_language, transcript_type = cls._resolve_interpretation_input(response)
        interpretation, _ = CandidateResponseInterpretation.objects.get_or_create(
            response=response,
            defaults={
                "session": response.session,
                "question": response.question,
            },
        )

        response.processing_status = "INTERPRETATION_PENDING"
        response.interpretation_status = "PROCESSING"
        response.interpretation_error = ""
        response.ai_processing_idempotency_key = idempotency_key
        response.save(
            update_fields=[
                "processing_status",
                "interpretation_status",
                "interpretation_error",
                "ai_processing_idempotency_key",
                "updated_at",
            ]
        )
        cls._log_event(
            actor=actor,
            action=AuditLogAction.INTERPRETATION_REQUESTED,
            description=f"Interpretation requested for response {response.public_id}",
            response=response,
            extra={"input_language": input_language, "input_transcript_type": transcript_type},
        )

        try:
            result = ResponseInterpretationService.interpret(
                response=response,
                transcript=input_transcript,
                input_language=input_language,
                input_transcript_type=transcript_type,
            )
        except AIProcessingError as exc:
            response.interpretation_status = "FAILED"
            response.interpretation_error = str(exc)
            response.processing_status = "FAILED"
            response.save(
                update_fields=[
                    "interpretation_status",
                    "interpretation_error",
                    "processing_status",
                    "updated_at",
                ]
            )
            interpretation.provider = settings.AI_INTERPRETATION_PROVIDER
            interpretation.model = ""
            interpretation.prompt_version = ResponseInterpretationService.prompt_version
            interpretation.prompt_hash = ""
            interpretation.idempotency_key = idempotency_key
            interpretation.input_transcript_type = transcript_type
            interpretation.input_language = input_language
            interpretation.input_transcript = input_transcript
            interpretation.status = "FAILED"
            interpretation.error_message = str(exc)
            interpretation.metadata = {
                "error_metadata": exc.metadata,
                "legal_disclaimer": LEGAL_DISCLAIMER_TEXT,
            }
            interpretation.legal_disclaimer = LEGAL_DISCLAIMER_TEXT
            interpretation.save()
            cls._log_event(
                actor=actor,
                action=AuditLogAction.INTERPRETATION_FAILED,
                description=f"Interpretation failed for response {response.public_id}",
                response=response,
                severity=AuditLogSeverity.ERROR,
                extra={"error_code": exc.code, "error_metadata": exc.metadata},
            )
            return response

        response.interpretation_status = "COMPLETED"
        response.interpretation_error = ""
        response.interpreted_at = timezone.now()
        response.processing_status = "INTERPRETATION_COMPLETED"
        response.save(
            update_fields=[
                "interpretation_status",
                "interpretation_error",
                "interpreted_at",
                "processing_status",
                "updated_at",
            ]
        )

        interpretation.provider = result["provider"]
        interpretation.model = result["model"]
        interpretation.prompt_version = result["prompt_version"]
        interpretation.prompt_hash = result["prompt_hash"]
        interpretation.idempotency_key = idempotency_key
        interpretation.input_transcript_type = transcript_type
        interpretation.input_language = input_language
        interpretation.input_transcript = input_transcript
        interpretation.structured_output = result["structured_output"]
        interpretation.normalized_indicators = result["normalized"]
        interpretation.risk_flags = result["normalized"]["risk_flags"]
        interpretation.confidence_score = result["confidence_score"]
        interpretation.status = "COMPLETED"
        interpretation.error_message = ""
        interpretation.metadata = {
            **result["metadata"],
            "legal_disclaimer": LEGAL_DISCLAIMER_TEXT,
        }
        interpretation.interpreted_at = response.interpreted_at
        interpretation.legal_disclaimer = LEGAL_DISCLAIMER_TEXT
        interpretation.save()

        cls._log_event(
            actor=actor,
            action=AuditLogAction.INTERPRETATION_COMPLETED,
            description=f"Interpretation completed for response {response.public_id}",
            response=response,
            extra={
                "provider": interpretation.provider,
                "model": interpretation.model,
                "prompt_version": interpretation.prompt_version,
                "prompt_hash": interpretation.prompt_hash,
                "confidence_score": str(interpretation.confidence_score) if interpretation.confidence_score is not None else None,
            },
        )
        return response

    @classmethod
    @transaction.atomic
    def prepare_evaluation_input(cls, *, response, actor=None, force=False, idempotency_key=""):
        if not settings.ENABLE_RULE_INPUT_PREPARATION:
            raise AIProcessingConfigurationError(
                "Rule input preparation is disabled",
                code="rule_input_preparation_disabled",
            )
        if response.interpretation_status != "COMPLETED":
            raise AIProcessingError(
                "Interpretation must complete before evaluation input preparation",
                code="interpretation_required",
            )
        if hasattr(response, "evaluation_input_artifact") and not force:
            response.processing_status = "RULE_INPUT_PREPARED"
            response.save(update_fields=["processing_status", "updated_at"])
            return response

        cls._log_event(
            actor=actor,
            action=AuditLogAction.RULE_INPUT_PREPARATION_STARTED,
            description=f"Rule input preparation started for response {response.public_id}",
            response=response,
        )

        interpretation = response.ai_interpretation
        payload = EvaluationInputBuilderService.build(response=response, interpretation=interpretation)
        artifact, _ = EvaluationInputArtifact.objects.get_or_create(
            response=response,
            defaults={
                "session": response.session,
                "question": response.question,
            },
        )
        artifact.session = response.session
        artifact.question = response.question
        artifact.competency_code = payload["competency_code"]
        artifact.idempotency_key = idempotency_key
        artifact.expected_indicators = payload["expected_indicators"]
        artifact.observed_indicators = payload["observed_indicators"]
        artifact.missing_indicators = payload["missing_indicators"]
        artifact.risk_flags = payload["risk_flags"]
        artifact.language_notes = payload["language_notes"]
        artifact.source_interpretation_status = interpretation.status
        artifact.requires_human_review = payload["requires_human_review"]
        artifact.review_reason = payload["review_reason"]
        artifact.legal_disclaimer = payload["legal_disclaimer"]
        artifact.metadata = payload["metadata"]
        artifact.prepared_at = timezone.now()
        artifact.save()

        response.processing_status = "RULE_INPUT_PREPARED"
        response.save(update_fields=["processing_status", "updated_at"])
        cls._log_event(
            actor=actor,
            action=AuditLogAction.RULE_INPUT_PREPARATION_COMPLETED,
            description=f"Rule input preparation completed for response {response.public_id}",
            response=response,
            extra={"artifact_id": str(artifact.public_id)},
        )
        if artifact.requires_human_review:
            cls._log_event(
                actor=actor,
                action=AuditLogAction.AI_PROCESSING_REQUIRES_HUMAN_REVIEW,
                description=f"AI processing flagged response {response.public_id} for human review",
                response=response,
                extra={"review_reason": artifact.review_reason},
            )
        return response

    @classmethod
    @transaction.atomic
    def process_response_ai(
        cls,
        *,
        response,
        actor=None,
        force=False,
        target_override="",
        idempotency_key="",
    ):
        resolved_idempotency_key = cls._resolve_idempotency_key(response=response, idempotency_key=idempotency_key)
        response.processing_status = "TRANSCRIPT_READY"
        response.ai_processing_idempotency_key = resolved_idempotency_key
        response.save(update_fields=["processing_status", "ai_processing_idempotency_key", "updated_at"])
        cls._log_event(
            actor=actor,
            action=AuditLogAction.AI_PROCESSING_STARTED,
            description=f"AI processing started for response {response.public_id}",
            response=response,
            extra={"idempotency_key": resolved_idempotency_key},
        )
        translated = cls.translate_response(
            response=response,
            actor=actor,
            force=force,
            target_override=target_override,
            idempotency_key=resolved_idempotency_key,
        )
        if translated.translation_status == "FAILED":
            return translated
        interpreted = cls.interpret_response(
            response=response,
            actor=actor,
            force=force,
            idempotency_key=resolved_idempotency_key,
        )
        if interpreted.interpretation_status == "FAILED":
            return interpreted
        prepared = cls.prepare_evaluation_input(
            response=response,
            actor=actor,
            force=force,
            idempotency_key=resolved_idempotency_key,
        )
        prepared.processing_status = "PROCESSING_COMPLETED"
        prepared.save(update_fields=["processing_status", "updated_at"])
        cls._log_event(
            actor=actor,
            action=AuditLogAction.AI_PROCESSING_COMPLETED,
            description=f"AI processing completed for response {response.public_id}",
            response=response,
            extra={"idempotency_key": resolved_idempotency_key},
        )
        return prepared

    @classmethod
    def auto_process_response_ai(
        cls,
        *,
        response,
        actor=None,
        target_override="",
    ):
        if settings.ENABLE_ASYNC_AI_PROCESSING and settings.AZURE_QUEUE_CONNECTION_STRING:
            try:
                _, updated = cls.queue_process_response_ai(
                    response=response,
                    actor=actor,
                    force=False,
                    target_override=target_override,
                )
                return updated
            except Exception:
                pass
        if not cls._sync_auto_processing_is_configured(response=response, target_override=target_override):
            return response
        return cls.process_response_ai(
            response=response,
            actor=actor,
            force=False,
            target_override=target_override,
        )

    @classmethod
    def _sync_auto_processing_is_configured(cls, *, response, target_override=""):
        provider = (settings.AI_INTERPRETATION_PROVIDER or "").upper()
        if provider == "OPENAI" and not settings.OPENAI_API_KEY:
            return False
        if provider == "GEMINI" and not settings.GEMINI_API_KEY:
            return False

        source_language = (
            response.transcript_language or response.session.stt_language_code or response.session.candidate_language
        )
        target_language = target_override or response.session.translation_target or source_language
        if source_language != target_language:
            translation_provider = (settings.TRANSLATION_PROVIDER or "").upper()
            if translation_provider == "GOOGLE" and not settings.GOOGLE_TRANSLATE_API_KEY:
                return False
        return True

    @classmethod
    @transaction.atomic
    def queue_process_response_ai(
        cls,
        *,
        response,
        actor=None,
        force=False,
        target_override="",
        idempotency_key="",
    ):
        if not settings.ENABLE_ASYNC_AI_PROCESSING:
            raise AIProcessingConfigurationError(
                "Async AI processing is disabled",
                code="async_ai_processing_disabled",
            )

        resolved_idempotency_key = cls._resolve_idempotency_key(
            response=response,
            idempotency_key=idempotency_key,
            async_execution=True,
        )
        existing_job = cls._get_async_job(response, idempotency_key=resolved_idempotency_key)
        if existing_job and existing_job.status in {"QUEUED", "PROCESSING", "COMPLETED"} and not force:
            response.ai_processing_idempotency_key = resolved_idempotency_key
            response.save(update_fields=["ai_processing_idempotency_key", "updated_at"])
            return existing_job, response

        job, _ = AIProcessingJob.objects.get_or_create(
            response=response,
            idempotency_key=resolved_idempotency_key,
            defaults={
                "session": response.session,
                "question": response.question,
                "job_type": "PROCESS_AI",
            },
        )
        job.session = response.session
        job.question = response.question
        job.job_type = "PROCESS_AI"
        job.status = "PENDING"
        job.error_message = ""
        job.metadata = {
            "force": force,
            "target_override": target_override,
            "legal_disclaimer": LEGAL_DISCLAIMER_TEXT,
        }
        job.save()

        enqueue_result = enqueue_background_job(
            "PROCESS_AI_RESPONSE",
            {
                "response_id": str(response.public_id),
                "idempotency_key": resolved_idempotency_key,
                "force": force,
                "target_override": target_override,
            },
        )
        if not enqueue_result.get("queued"):
            job.status = "FAILED"
            job.error_message = enqueue_result.get("reason", "Async queueing failed")
            job.metadata = {
                **job.metadata,
                "enqueue_result": enqueue_result,
            }
            job.save(update_fields=["status", "error_message", "metadata", "updated_at"])
            raise AIProcessingConfigurationError(
                "Async AI processing queue is not configured",
                code="async_queue_not_configured",
                metadata=enqueue_result,
            )

        job.status = "QUEUED"
        job.queue_name = enqueue_result.get("queue", "")
        job.queue_message_id = enqueue_result.get("message_id", "")
        job.metadata = {
            **job.metadata,
            "enqueue_result": enqueue_result,
        }
        job.save(update_fields=["status", "queue_name", "queue_message_id", "metadata", "updated_at"])

        response.processing_status = "QUEUED"
        response.ai_processing_idempotency_key = resolved_idempotency_key
        response.save(update_fields=["processing_status", "ai_processing_idempotency_key", "updated_at"])
        cls._log_event(
            actor=actor,
            action=AuditLogAction.AI_PROCESSING_QUEUED,
            description=f"AI processing queued for response {response.public_id}",
            response=response,
            extra={
                "idempotency_key": resolved_idempotency_key,
                "queue_name": job.queue_name,
                "queue_message_id": job.queue_message_id,
            },
        )
        return job, response

    @classmethod
    @transaction.atomic
    def run_async_job(cls, *, job, actor=None):
        if job.status == "COMPLETED":
            return job.response
        job.status = "PROCESSING"
        job.started_at = timezone.now()
        job.error_message = ""
        job.save(update_fields=["status", "started_at", "error_message", "updated_at"])
        response = job.response
        updated_response = cls.process_response_ai(
            response=response,
            actor=actor,
            force=bool(job.metadata.get("force")),
            target_override=job.metadata.get("target_override", ""),
            idempotency_key=job.idempotency_key,
        )
        job.status = "COMPLETED" if updated_response.processing_status == "PROCESSING_COMPLETED" else "FAILED"
        job.completed_at = timezone.now()
        if job.status == "FAILED":
            job.error_message = updated_response.interpretation_error or updated_response.translation_error
        job.save(update_fields=["status", "completed_at", "error_message", "updated_at"])
        return updated_response

    @classmethod
    def get_response_status_payload(cls, response):
        translation = getattr(response, "translation_artifact", None)
        interpretation = getattr(response, "ai_interpretation", None)
        evaluation_input = getattr(response, "evaluation_input_artifact", None)
        async_job = cls._get_async_job(response, idempotency_key=response.ai_processing_idempotency_key)
        return {
            "candidate_response_id": str(response.public_id),
            "session_id": str(response.session.public_id),
            "question_id": str(response.question.public_id),
            "translation_status": response.translation_status,
            "interpretation_status": response.interpretation_status,
            "processing_status": response.processing_status,
            "ai_processing_idempotency_key": response.ai_processing_idempotency_key,
            "translation": translation,
            "interpretation": interpretation,
            "evaluation_input": evaluation_input,
            "async_job": async_job,
        }

    @classmethod
    def get_session_summary_payload(cls, session):
        responses = (
            CandidateResponse.objects.filter(session=session)
            .select_related("translation_artifact", "ai_interpretation", "evaluation_input_artifact", "question")
            .order_by("question__question_order", "created_at")
        )
        items = []
        for response in responses:
            items.append(
                {
                    "candidate_response_id": str(response.public_id),
                    "question_id": str(response.question.public_id),
                    "question_order": response.question.question_order,
                    "translation_status": response.translation_status,
                    "interpretation_status": response.interpretation_status,
                    "processing_status": response.processing_status,
                    "rule_input_prepared": hasattr(response, "evaluation_input_artifact"),
                    "requires_human_review": getattr(
                        getattr(response, "evaluation_input_artifact", None),
                        "requires_human_review",
                        False,
                    ),
                }
            )
        completed = bool(items) and all(
            item["processing_status"] == "PROCESSING_COMPLETED" for item in items
        )
        return {
            "session_id": str(session.public_id),
            "completed": completed,
            "responses": items,
        }
