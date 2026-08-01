import random
from collections import Counter

from django.db import transaction
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings

from api.audit.services import AuditLogService
from api.core.constants import (
    AuditLogAction,
    AuditLogCategory,
    AuditLogSeverity,
    CandidateResponseType,
    IdentityVerificationStatus,
    InterviewEvaluationTier,
    InterviewSessionStatus,
    QuestionLifecycleStatus,
    SessionObservedTaskStatus,
    SessionQuestionStatus,
    TaskObservationResultStatus,
    EvaluationStatus,
    EvaluationType,
)
from api.evaluations.models import Evaluation
from api.interviews.package_services import PackageArchitectureService
from api.interviews.voice_services import (
    SpeechToTextService,
    TextToSpeechService,
    VoiceProviderConfigurationError,
    VoiceProviderError,
)
from api.translation.services import AIProcessingError, TranslationService
from api.questions.models import QuestionTemplate
from api.storage.services import MediaStorageService

from .models import (
    CandidateResponse,
    IntegrityLog,
    InterviewSession,
    ObservedTaskDefinition,
    QuestionAudioArtifact,
    SessionObservedTask,
    SessionQuestion,
    TaskObservationResult,
)
from .realtime import InterviewSessionSocketRegistry


LANGUAGE_CODE_MAP = {
    "EN": "en-US",
    "AR": "ar-SA",
    "AM": "am-ET",
    # Afaan Oromo has no working TTS/STT provider (confirmed directly
    # against both - Google TTS rejects it outright, Whisper doesn't list
    # it as a supported language). Mapped here for completeness/consistency
    # (ui_language/translation_target still use this), but the frontend
    # deliberately never offers it for read-aloud and forces text-only
    # answers for Oromo sessions - see READ_ALOUD_LANGUAGES and the
    # answer-mode gating in page.tsx.
    "OM": "om-ET",
    "FIL": "fil-PH",
    "ZH": "zh-CN",
    # GCC top labor-source-country languages - all confirmed directly
    # against Google TTS (200) and Google Translate (200) before being
    # added here; Whisper's documented supported-language list also covers
    # all of these, unlike Oromo/Tigrinya/Afar/Twi/Luganda/Malagasy.
    "HI": "hi-IN",
    "UR": "ur-PK",
    "BN": "bn-BD",
    "TA": "ta-IN",
    "TE": "te-IN",
    "ML": "ml-IN",
    "PA": "pa-IN",
    "SI": "si-LK",
    "ID": "id-ID",
    "NE": "ne-NP",
    "SW": "sw-KE",
    "VI": "vi-VN",
    "KM": "km-KH",
    "MY": "my-MM",
    # Second-tier GCC labor-source-country languages - Google TTS rejects
    # all six outright (confirmed directly, HTTP 400 on every one), so none
    # of these are ever offered for read-aloud (see READ_ALOUD_LANGUAGES,
    # which filters on the `tts` flag). Google Translate supports all six
    # (200), so text answers + translation work regardless. Somali and
    # Malagasy are in Whisper's documented supported-language list, so
    # audio answers work for those two (`stt: true` in the frontend
    # LANGUAGES list); Tigrinya/Afar/Twi/Luganda aren't, so those four are
    # forced text-only the same way Oromo is.
    "TI": "ti-ET",
    "SO": "so-SO",
    "AA": "aa-DJ",
    "AK": "ak-GH",
    "LG": "lg-UG",
    "MG": "mg-MG",
}


def session_event_payload(session, extra=None):
    answered_count = session.questions.filter(status=SessionQuestionStatus.ANSWERED).count()
    payload = {
        "session_id": str(session.public_id),
        "status": session.status,
        "current_question_index": session.current_question_index,
        "total_questions": session.total_questions,
        "answered_questions": answered_count,
    }
    if extra:
        payload.update(extra)
    return payload


def voice_event_payload(session, *, question=None, response=None, extra=None, access_context="staff"):
    payload = session_event_payload(
        session,
        {
            "access_context": access_context,
        },
    )
    if question is not None:
        payload["question_id"] = str(question.public_id)
        payload["question_order"] = question.question_order
    if response is not None:
        payload["candidate_response_id"] = str(response.public_id)
    if extra:
        payload.update(extra)
    return payload


class QuestionGenerationService:
    @classmethod
    def generate_questions(cls, session):
        if session.questions.exists():
            return list(session.questions.order_by("question_order"))

        target_count = session.config.total_questions
        tier = session.evaluation_tier or InterviewEvaluationTier.FULL
        queryset = QuestionTemplate.objects.filter(
            is_active=True,
            question_status=QuestionLifecycleStatus.ACTIVE,
            evaluation_tier__in=[tier, InterviewEvaluationTier.BOTH],
        )
        if session.role_code:
            queryset = queryset.filter(role_code__iexact=session.role_code)
        else:
            queryset = queryset.filter(role_name__iexact=session.role_name)
        localized = list(queryset.filter(language=session.candidate_language))
        if len(localized) < target_count:
            localized = list(queryset)
        if len(localized) < target_count:
            localized = list(QuestionTemplate.objects.filter(is_active=True))

        selected_templates = cls._select_templates(session, localized, target_count)
        questions = []
        for order, template in enumerate(selected_templates, start=1):
            questions.append(
                SessionQuestion(
                    session=session,
                    question_template=template,
                    question_text=template.question_text,
                    domain=template.domain,
                    skill=template.skill_tag or template.skill,
                    difficulty=template.difficulty,
                    question_order=order,
                    is_mandatory=template.is_mandatory,
                )
            )

        SessionQuestion.objects.bulk_create(questions)
        session.total_questions = len(questions)
        session.save(update_fields=["total_questions", "updated_at"])
        return list(session.questions.order_by("question_order"))

    @classmethod
    def _select_templates(cls, session, templates, target_count):
        if not templates:
            return []

        template_pool = cls._dedupe_templates(templates)
        candidate_skills = {
            skill.strip().lower()
            for skill in session.candidate.get_skills_list()
            if skill.strip()
        }
        previous_context = cls._build_candidate_history(session)
        selected = []
        selected_context = {
            "domains": Counter(),
            "skills": Counter(),
            "question_types": Counter(),
            "difficulties": Counter(),
            "has_critical": False,
        }

        while len(selected) < target_count and template_pool:
            scored = [
                (
                    cls._score_template(
                        session=session,
                        template=template,
                        candidate_skills=candidate_skills,
                        previous_context=previous_context,
                        selected_context=selected_context,
                    ),
                    random.random(),
                    template,
                )
                for template in template_pool
            ]
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            chosen = scored[0][2]
            selected.append(chosen)
            cls._update_selected_context(selected_context, chosen)
            template_pool = [
                template
                for template in template_pool
                if template.id != chosen.id and not cls._is_same_prompt_family(template, chosen)
            ]

        return selected

    @classmethod
    def _dedupe_templates(cls, templates):
        best_by_key = {}
        for template in templates:
            key = (
                (template.question_code or "").strip().lower() or str(template.id),
                (template.language or "").strip().lower(),
            )
            current = best_by_key.get(key)
            if current is None or cls._template_specificity_score(template) > cls._template_specificity_score(current):
                best_by_key[key] = template
        return list(best_by_key.values())

    @classmethod
    def _template_specificity_score(cls, template):
        return (
            (20 if template.role_code else 0)
            + (10 if template.language else 0)
            + (8 if template.rubric_version else 0)
            + (8 if template.question_set_version else 0)
            + (6 if template.question_code else 0)
            + (4 if template.skill_tag else 0)
            + (2 if template.critical_question else 0)
        )

    @classmethod
    def _build_candidate_history(cls, session):
        prior_questions = (
            SessionQuestion.objects.filter(session__candidate=session.candidate)
            .exclude(session=session)
            .select_related("question_template")
        )
        question_codes = Counter()
        domains = Counter()
        skills = Counter()
        question_types = Counter()
        for prior in prior_questions:
            template = prior.question_template
            question_code = ((template.question_code if template else "") or "").strip().lower()
            domain = (prior.domain or "").strip().lower()
            skill = (prior.skill or "").strip().lower()
            question_type = ((template.question_type if template else "") or "").strip().lower()
            if question_code:
                question_codes[question_code] += 1
            if domain:
                domains[domain] += 1
            if skill:
                skills[skill] += 1
            if question_type:
                question_types[question_type] += 1
        return {
            "question_codes": question_codes,
            "domains": domains,
            "skills": skills,
            "question_types": question_types,
        }

    @classmethod
    def _score_template(cls, *, session, template, candidate_skills, previous_context, selected_context):
        score = 0
        template_language = (template.language or "").strip().lower()
        session_language = (session.candidate_language or "").strip().lower()
        domain = (template.domain or "").strip().lower()
        skill = (template.skill_tag or template.skill or "").strip().lower()
        question_type = (template.question_type or "").strip().lower()
        difficulty = (template.difficulty or "").strip().upper()
        question_code = (template.question_code or "").strip().lower()

        if template_language == session_language:
            score += 40
        if session.rubric_version and template.rubric_version == session.rubric_version:
            score += 16
        if session.question_set_version and template.question_set_version == session.question_set_version:
            score += 16
        if skill and any(candidate_skill in skill or skill in candidate_skill for candidate_skill in candidate_skills):
            score += 24
        elif domain and any(candidate_skill in domain for candidate_skill in candidate_skills):
            score += 12
        if template.critical_question and not selected_context["has_critical"]:
            score += 14
        if template.is_mandatory:
            score += 6

        score -= previous_context["question_codes"].get(question_code, 0) * 80
        score -= previous_context["domains"].get(domain, 0) * 12
        score -= previous_context["skills"].get(skill, 0) * 14
        score -= previous_context["question_types"].get(question_type, 0) * 8

        score -= selected_context["domains"].get(domain, 0) * 18
        score -= selected_context["skills"].get(skill, 0) * 22
        score -= selected_context["question_types"].get(question_type, 0) * 14
        score -= selected_context["difficulties"].get(difficulty, 0) * 4

        if difficulty == "MEDIUM":
            score += 4
        return score

    @classmethod
    def _update_selected_context(cls, selected_context, template):
        domain = (template.domain or "").strip().lower()
        skill = (template.skill_tag or template.skill or "").strip().lower()
        question_type = (template.question_type or "").strip().lower()
        difficulty = (template.difficulty or "").strip().upper()
        if domain:
            selected_context["domains"][domain] += 1
        if skill:
            selected_context["skills"][skill] += 1
        if question_type:
            selected_context["question_types"][question_type] += 1
        if difficulty:
            selected_context["difficulties"][difficulty] += 1
        if template.critical_question:
            selected_context["has_critical"] = True

    @classmethod
    def _is_same_prompt_family(cls, left, right):
        left_code = (left.question_code or "").strip().lower()
        right_code = (right.question_code or "").strip().lower()
        if left_code and right_code and left_code == right_code:
            return True
        left_skill = (left.skill_tag or left.skill or "").strip().lower()
        right_skill = (right.skill_tag or right.skill or "").strip().lower()
        left_domain = (left.domain or "").strip().lower()
        right_domain = (right.domain or "").strip().lower()
        return bool(
            left_skill
            and right_skill
            and left_domain
            and right_domain
            and left_skill == right_skill
            and left_domain == right_domain
            and left.question_text.strip().lower() == right.question_text.strip().lower()
        )


class InterviewSessionService:
    @classmethod
    @transaction.atomic
    def create_session(cls, *, candidate, config, created_by, package_code="", scheduled_start_at=None):
        language = candidate.preferred_language or config.language or "EN"
        package_context = PackageArchitectureService.resolve_session_package_context(
            user=created_by,
            role_code=config.role_code,
            requested_package_code=package_code,
        )
        session_evaluation_tier = config.evaluation_tier
        package_session_config = None
        resolved_package_code = ""
        resolved_package_name = ""
        coverage_level = "FULL"
        task_observation_enabled = config.enable_task_module
        readiness_indicator_enabled = config.evaluation_tier == InterviewEvaluationTier.FULL
        certificate_enabled = config.evaluation_tier == InterviewEvaluationTier.FULL
        expiry_duration = config.duration_minutes

        if package_context is not None:
            package_session_config = package_context["package"]
            coverage = package_context["coverage"]
            session_evaluation_tier = package_context["evaluation_tier"]
            resolved_package_code = package_session_config.package_code
            resolved_package_name = package_session_config.package_name
            coverage_level = coverage.coverage_level
            task_observation_enabled = package_context["task_observation_enabled"]
            readiness_indicator_enabled = package_context["readiness_indicator_enabled"]
            certificate_enabled = package_context["certificate_enabled"]
            expiry_duration = package_session_config.duration_minutes or config.duration_minutes

        expiry_anchor = scheduled_start_at or timezone.now()

        session = InterviewSession.objects.create(
            candidate=candidate,
            organization=candidate.company,
            config=config,
            package_session_config=package_session_config,
            role_name=config.role_name or candidate.get_job_role_display(),
            role_code=config.role_code,
            ui_language=config.language or language,
            candidate_language=language,
            tts_language_code=LANGUAGE_CODE_MAP.get(language, "en-US"),
            stt_language_code=LANGUAGE_CODE_MAP.get(language, "en-US"),
            translation_target=config.language if config.enable_translation else "",
            total_questions=config.total_questions,
            evaluation_tier=session_evaluation_tier,
            package_code=resolved_package_code,
            package_name=resolved_package_name,
            coverage_level=coverage_level,
            task_observation_enabled=task_observation_enabled,
            readiness_indicator_enabled=readiness_indicator_enabled,
            certificate_enabled=certificate_enabled,
            rubric_version=config.rubric_version,
            question_set_version=config.question_set_version,
            scheduled_start_at=scheduled_start_at,
            expires_at=InterviewSession.build_expiry(expiry_duration, anchor_time=expiry_anchor),
            created_by=created_by,
        )
        QuestionGenerationService.generate_questions(session)
        TaskObservationService.assign_tasks(session)
        cls._ensure_linked_evaluation(session, status=EvaluationStatus.SCHEDULED)
        AuditLogService.log(
            user=created_by,
            action=AuditLogAction.SESSION_CREATED,
            category=AuditLogCategory.SESSION,
            description=f"Interview session created for {candidate.get_full_name()}",
            resource=session,
            data=session_event_payload(session, {"candidate_id": str(candidate.public_id)}),
        )
        InterviewSessionSocketRegistry.broadcast_sync(
            str(session.public_id),
            {"event": "SESSION_CREATED", **session_event_payload(session)},
        )
        cls._broadcast_to_channel_layer(
            str(session.public_id),
            {"event": "SESSION_CREATED", **session_event_payload(session)},
        )
        return session

    @classmethod
    @transaction.atomic
    def reschedule_session(cls, session, *, scheduled_start_at, actor=None):
        if session.status in {
            InterviewSessionStatus.IN_PROGRESS,
            InterviewSessionStatus.COMPLETED,
            InterviewSessionStatus.FAILED,
            InterviewSessionStatus.EXPIRED,
            InterviewSessionStatus.CANCELLED,
        }:
            raise ValueError("Only pending interview sessions can be rescheduled")

        duration_minutes = (
            session.package_session_config.duration_minutes
            if session.package_session_config and session.package_session_config.duration_minutes
            else session.config.duration_minutes
        )
        session.scheduled_start_at = scheduled_start_at
        session.expires_at = InterviewSession.build_expiry(duration_minutes, anchor_time=scheduled_start_at)
        session.token_expires_at = session.expires_at
        session.save(update_fields=["scheduled_start_at", "expires_at", "token_expires_at", "updated_at"])

        cls._ensure_linked_evaluation(session, status=EvaluationStatus.SCHEDULED)

        if actor:
            AuditLogService.log(
                user=actor,
                action="SESSION_RESCHEDULED",
                category=AuditLogCategory.SESSION,
                description=f"Interview session rescheduled for {session.candidate.get_full_name()}",
                resource=session,
                data=session_event_payload(session, {"scheduled_start_at": session.scheduled_start_at.isoformat()}),
            )
        InterviewSessionSocketRegistry.broadcast_sync(
            str(session.public_id),
            {"event": "SESSION_RESCHEDULED", **session_event_payload(session, {"scheduled_start_at": session.scheduled_start_at.isoformat()})},
        )
        cls._broadcast_to_channel_layer(
            str(session.public_id),
            {"event": "SESSION_RESCHEDULED", **session_event_payload(session, {"scheduled_start_at": session.scheduled_start_at.isoformat()})},
        )
        return session

    @classmethod
    @transaction.atomic
    def cancel_session(cls, session, *, reason="", actor=None):
        if session.status in {
            InterviewSessionStatus.IN_PROGRESS,
            InterviewSessionStatus.COMPLETED,
            InterviewSessionStatus.FAILED,
            InterviewSessionStatus.EXPIRED,
        }:
            raise ValueError("Closed interview sessions cannot be cancelled")
        if session.status == InterviewSessionStatus.CANCELLED:
            raise ValueError("Interview session is already cancelled")

        now = timezone.now()
        session.status = InterviewSessionStatus.CANCELLED
        session.cancelled_at = now
        session.cancellation_reason = reason or ""
        session.ended_at = session.ended_at or now
        session.last_activity_at = now
        session.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancellation_reason",
                "ended_at",
                "last_activity_at",
                "updated_at",
            ]
        )

        evaluation = cls._ensure_linked_evaluation(session, status=EvaluationStatus.CANCELLED)
        evaluation.cancelled_at = now
        evaluation.cancellation_reason = reason or ""
        evaluation.save(update_fields=["cancelled_at", "cancellation_reason", "updated_at"])

        if actor:
            AuditLogService.log(
                user=actor,
                action=AuditLogAction.SESSION_CANCELLED,
                category=AuditLogCategory.SESSION,
                description=f"Interview session cancelled for {session.candidate.get_full_name()}",
                resource=session,
                data=session_event_payload(session, {"cancellation_reason": session.cancellation_reason}),
            )
        InterviewSessionSocketRegistry.broadcast_sync(
            str(session.public_id),
            {"event": "SESSION_CANCELLED", **session_event_payload(session, {"cancellation_reason": session.cancellation_reason})},
        )
        cls._broadcast_to_channel_layer(
            str(session.public_id),
            {"event": "SESSION_CANCELLED", **session_event_payload(session, {"cancellation_reason": session.cancellation_reason})},
        )
        return session

    @classmethod
    @transaction.atomic
    def start_session(cls, session, actor=None):
        if session.is_expired():
            session.status = InterviewSessionStatus.EXPIRED
            session.save(update_fields=["status", "updated_at"])
            raise ValueError("Cannot start expired session")
        if session.status == InterviewSessionStatus.COMPLETED:
            raise ValueError("Cannot restart completed session")

        events = []
        token_start = actor is None
        if token_start and not session.candidate_prechecks_complete():
            raise ValueError(
                "Candidate session cannot start until consent, identity verification, device check, verbal confirmation, and privacy notice acknowledgement are complete"
            )

        if session.status == InterviewSessionStatus.CREATED and not token_start:
            session.verification_status = IdentityVerificationStatus.PENDING
            session.status = InterviewSessionStatus.VERIFICATION_PENDING
            session.save(update_fields=["status", "verification_status", "updated_at"])
            events.append(("SESSION_VERIFICATION_PENDING", AuditLogAction.SESSION_VERIFICATION_PENDING))

            if session.candidate_prechecks_complete():
                session.status = InterviewSessionStatus.READY
                session.save(update_fields=["status", "updated_at"])
                events.append(("SESSION_READY", AuditLogAction.SESSION_READY))
            else:
                # Staff still needs to be able to move a session to READY even
                # though most of the precheck battery (consent, device check,
                # verbal confirmation, privacy notice) has no candidate-facing
                # UI yet - but identity verification now does (the webcam
                # precheck screen on the candidate interview page), so unlike
                # before, this no longer fakes identity_verified/
                # verification_status/single_face_detected. The candidate
                # goes through the real check themselves when they open their
                # session link; leaving identity_verified at its default
                # False is what makes that screen actually trigger.
                session.status = InterviewSessionStatus.READY
                session.save(update_fields=["status", "updated_at"])
                events.append(("SESSION_READY", AuditLogAction.SESSION_READY))
        elif session.status == InterviewSessionStatus.CREATED and token_start:
            session.status = InterviewSessionStatus.READY
            session.save(update_fields=["status", "updated_at"])
            events.append(("SESSION_READY", AuditLogAction.SESSION_READY))

        session.start()
        cls._ensure_linked_evaluation(session, status=EvaluationStatus.IN_PROGRESS)
        events.append(("SESSION_STARTED", AuditLogAction.SESSION_STARTED))
        cls._log_and_broadcast(actor, session, events)
        return session

    @classmethod
    @transaction.atomic
    def get_next_question(cls, session, actor=None):
        return cls.get_or_activate_current_question(session, actor=actor)

    @classmethod
    @transaction.atomic
    def get_or_activate_current_question(cls, session, actor=None):
        if session.is_closed():
            raise ValueError("Cannot fetch questions from a closed session")
        if session.status != InterviewSessionStatus.IN_PROGRESS:
            raise ValueError("Session must be in progress")

        current_question = session.questions.filter(status=SessionQuestionStatus.ASKED).first()
        if current_question:
            return current_question, False

        next_question = session.questions.filter(status=SessionQuestionStatus.PENDING).order_by("question_order").first()
        if next_question is None:
            cls.complete_session(session, actor=actor)
            return None, True

        next_question.status = SessionQuestionStatus.ASKED
        next_question.asked_at = timezone.now()
        next_question.save(update_fields=["status", "asked_at", "updated_at"])

        session.current_question_index = next_question.question_order
        session.last_activity_at = timezone.now()
        session.save(update_fields=["current_question_index", "last_activity_at", "updated_at"])

        cls._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.QUESTION_SENT,
            category=AuditLogCategory.QUESTION,
            description=f"Question {next_question.question_order} sent for session {session.public_id}",
            resource=session,
            data=voice_event_payload(session, question=next_question),
        )
        cls._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.SESSION_MOVED_TO_NEXT_QUESTION,
            category=AuditLogCategory.VOICE,
            description=f"Session moved to question {next_question.question_order}",
            resource=session,
            data=voice_event_payload(session, question=next_question),
        )

        InterviewSessionSocketRegistry.broadcast_sync(
            str(session.public_id),
            {
                "event": "QUESTION_ASKED",
                **session_event_payload(
                    session,
                    {
                        "question_id": str(next_question.public_id),
                        "order": next_question.question_order,
                        "text": next_question.question_text,
                        "difficulty": next_question.difficulty,
                        "skill": next_question.skill,
                        "domain": next_question.domain,
                    },
                ),
            },
        )
        cls._broadcast_to_channel_layer(
            str(session.public_id),
            {
                "event": "QUESTION_ASKED",
                **session_event_payload(
                    session,
                    {
                        "question_id": str(next_question.public_id),
                        "order": next_question.question_order,
                        "text": next_question.question_text,
                        "difficulty": next_question.difficulty,
                        "skill": next_question.skill,
                        "domain": next_question.domain,
                    },
                ),
            },
        )
        return next_question, False

    @classmethod
    @transaction.atomic
    def submit_response(
        cls,
        session,
        question,
        *,
        transcript,
        actor=None,
        response_type=CandidateResponseType.TEXT,
        text_response="",
        duration_seconds=0,
        metadata=None,
        language_code=None,
    ):
        if session.is_closed():
            raise ValueError("Cannot answer after session is closed")
        if session.status != InterviewSessionStatus.IN_PROGRESS:
            raise ValueError("Session must be in progress")
        if question.session_id != session.id:
            raise ValueError("Question does not belong to this session")
        if question.status != SessionQuestionStatus.ASKED:
            if question.is_mandatory and question.status == SessionQuestionStatus.PENDING:
                raise ValueError("Cannot skip mandatory question")
            raise ValueError("Question is not currently active")
        if language_code and language_code not in LANGUAGE_CODE_MAP.values():
            raise ValueError(f"Unsupported answer language: {language_code}")

        attempt_number = question.responses.count() + 1
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type=response_type,
            transcript=transcript,
            original_transcript=transcript,
            # Candidate-selected language for this specific answer, when
            # given, overrides the session's fixed default - this is also
            # what the translation pipeline (translate_response) later
            # reads as its source language, so an accurate per-answer value
            # here matters beyond just display.
            transcript_language=language_code or session.candidate_language or session.stt_language_code,
            text_response=text_response or transcript,
            duration_seconds=duration_seconds,
            attempt_number=attempt_number,
            metadata=metadata or {},
            stt_status="COMPLETED" if response_type == CandidateResponseType.TEXT else "PENDING",
            processing_status="TRANSCRIPT_READY" if transcript else "NOT_STARTED",
        )

        question.status = SessionQuestionStatus.ANSWERED
        question.answered_at = timezone.now()
        question.save(update_fields=["status", "answered_at", "updated_at"])

        session.last_activity_at = timezone.now()
        session.current_question_index = question.question_order
        session.save(update_fields=["last_activity_at", "current_question_index", "updated_at"])

        if actor:
            AuditLogService.log(
                user=actor,
                action=AuditLogAction.ANSWER_SUBMITTED,
                category=AuditLogCategory.SESSION,
                description=f"Answer submitted for question {question.question_order}",
                resource=session,
                data=session_event_payload(
                    session,
                    {
                        "question_id": str(question.public_id),
                        "response_id": str(response.public_id),
                    },
                ),
            )

        InterviewSessionSocketRegistry.broadcast_sync(
            str(session.public_id),
            {
                "event": "ANSWER_RECEIVED",
                **session_event_payload(
                    session,
                    {
                        "question_id": str(question.public_id),
                        "response_id": str(response.public_id),
                    },
                ),
            },
        )
        cls._broadcast_to_channel_layer(
            str(session.public_id),
            {
                "event": "ANSWER_RECEIVED",
                **session_event_payload(
                    session,
                    {
                        "question_id": str(question.public_id),
                        "response_id": str(response.public_id),
                    },
                ),
            },
        )
        if transcript:
            from api.translation.services import AIProcessingOrchestrationService

            AIProcessingOrchestrationService.auto_process_response_ai(
                response=response,
                actor=actor,
                target_override=session.translation_target,
            )
        return response

    @classmethod
    @transaction.atomic
    def complete_session(cls, session, actor=None):
        session.complete()
        evaluation = cls._ensure_linked_evaluation(session, status=EvaluationStatus.IN_PROGRESS)
        if evaluation.status != EvaluationStatus.COMPLETED:
            evaluation.complete()
            if not evaluation.certificate_enabled:
                evaluation.certificate_status = "NOT_ISSUED"
            if not evaluation.readiness_indicator_enabled:
                evaluation.readiness_status = "PENDING"
                evaluation.readiness_override_applied = False
                evaluation.readiness_override_reason = ""
            evaluation.save()
        if actor:
            AuditLogService.log(
                user=actor,
                action=AuditLogAction.SESSION_COMPLETED,
                category=AuditLogCategory.SESSION,
                description=f"Interview session completed for {session.candidate.get_full_name()}",
                resource=session,
                data=session_event_payload(session),
            )
        InterviewSessionSocketRegistry.broadcast_sync(
            str(session.public_id),
            {"event": "SESSION_COMPLETED", **session_event_payload(session)},
        )
        cls._broadcast_to_channel_layer(
            str(session.public_id),
            {"event": "SESSION_COMPLETED", **session_event_payload(session)},
        )
        return session

    @classmethod
    def _ensure_linked_evaluation(cls, session, status=EvaluationStatus.SCHEDULED):
        target_scheduled_date = session.scheduled_start_at or session.started_at or timezone.now()
        evaluation, created = Evaluation.objects.get_or_create(
            session=session,
            defaults={
                "candidate": session.candidate,
                "evaluation_type": EvaluationType.INTERVIEW,
                "status": status,
                "scheduled_date": target_scheduled_date,
                "duration_minutes": session.package_session_config.duration_minutes if session.package_session_config else session.config.duration_minutes,
                "created_by": session.created_by,
                "company": session.organization,
                "evaluation_tier": session.evaluation_tier,
                "package_code": session.package_code,
                "coverage_level": session.coverage_level,
                "readiness_indicator_enabled": session.readiness_indicator_enabled,
                "certificate_enabled": session.certificate_enabled,
            },
        )
        changed_fields = []
        if evaluation.status != status and evaluation.status != EvaluationStatus.COMPLETED:
            evaluation.status = status
            changed_fields.append("status")
        if evaluation.scheduled_date != target_scheduled_date and evaluation.status != EvaluationStatus.COMPLETED:
            evaluation.scheduled_date = target_scheduled_date
            changed_fields.append("scheduled_date")
        if evaluation.evaluation_tier != session.evaluation_tier:
            evaluation.evaluation_tier = session.evaluation_tier
            changed_fields.append("evaluation_tier")
        if evaluation.package_code != session.package_code:
            evaluation.package_code = session.package_code
            changed_fields.append("package_code")
        if evaluation.coverage_level != session.coverage_level:
            evaluation.coverage_level = session.coverage_level
            changed_fields.append("coverage_level")
        if evaluation.readiness_indicator_enabled != session.readiness_indicator_enabled:
            evaluation.readiness_indicator_enabled = session.readiness_indicator_enabled
            changed_fields.append("readiness_indicator_enabled")
        if evaluation.certificate_enabled != session.certificate_enabled:
            evaluation.certificate_enabled = session.certificate_enabled
            changed_fields.append("certificate_enabled")
        if changed_fields:
            changed_fields.append("updated_at")
            evaluation.save(update_fields=changed_fields)
        return evaluation

    @classmethod
    def log_integrity_event(cls, session, *, event_type, severity="INFO", details=None):
        return IntegrityLog.objects.create(
            session=session,
            candidate=session.candidate,
            event_type=event_type,
            severity=severity,
            details=details or {},
        )

    @classmethod
    def _log_and_broadcast(cls, actor, session, events):
        for event_name, action in events:
            if actor:
                AuditLogService.log(
                    user=actor,
                    action=action,
                    category=AuditLogCategory.SESSION,
                    description=event_name.replace("_", " ").title(),
                    resource=session,
                    data=session_event_payload(session),
                )
            InterviewSessionSocketRegistry.broadcast_sync(
                str(session.public_id),
                {"event": event_name, **session_event_payload(session)},
            )
            cls._broadcast_to_channel_layer(
                str(session.public_id),
                {"event": event_name, **session_event_payload(session)},
            )

    @classmethod
    def _broadcast_to_channel_layer(cls, session_public_id, payload):
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(
            f"interview_session_{session_public_id}",
            {
                "type": "session.event",
                "payload": payload,
            },
        )

    @classmethod
    def _log_voice_or_session_event(
        cls,
        *,
        actor,
        action,
        category,
        description,
        resource,
        data,
        severity=AuditLogSeverity.INFO,
    ):
        if actor:
            AuditLogService.log(
                user=actor,
                action=action,
                category=category,
                description=description,
                resource=resource,
                severity=severity,
                data=data,
            )
            return

        AuditLogService.log_system(
            action=action,
            category=category,
            description=description,
            resource=resource,
            severity=severity,
            data=data,
        )


class TaskObservationService:
    @classmethod
    @transaction.atomic
    def assign_tasks(cls, session):
        if not session.task_observation_enabled:
            return []
        if session.observed_tasks.exists():
            return list(session.observed_tasks.select_related("task_definition").order_by("task_order"))

        queryset = ObservedTaskDefinition.objects.filter(is_active=True)
        if session.role_code:
            scoped = queryset.filter(role_code__iexact=session.role_code)
            definitions = list(scoped) or list(queryset.filter(role_code=""))
        else:
            definitions = list(queryset.filter(role_code=""))

        tasks = [
            SessionObservedTask(
                session=session,
                candidate=session.candidate,
                task_definition=definition,
                task_order=index,
                status=SessionObservedTaskStatus.READY,
            )
            for index, definition in enumerate(definitions, start=1)
        ]
        if tasks:
            SessionObservedTask.objects.bulk_create(tasks)
        return list(session.observed_tasks.select_related("task_definition").order_by("task_order"))

    @classmethod
    @transaction.atomic
    def start_task(cls, *, session, actor=None):
        cls._ensure_task_observation_enabled(session)
        cls._ensure_session_active(session)
        task = cls._get_current_or_next_task(session)
        if task is None:
            raise ValueError("No observed tasks are assigned to this session.")
        if task.status == SessionObservedTaskStatus.IN_PROGRESS:
            return task

        task.status = SessionObservedTaskStatus.IN_PROGRESS
        if task.started_at is None:
            task.started_at = timezone.now()
        task.attempt_count += 1
        task.save(update_fields=["status", "started_at", "attempt_count", "updated_at"])

        access_context = "staff" if actor else "session_token"
        InterviewSessionService._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.TASK_OBSERVATION_STARTED,
            category=AuditLogCategory.SESSION,
            description=f"Task observation started for task {task.task_definition.task_code}",
            resource=session,
            data=cls._task_event_payload(session, task, access_context=access_context),
        )
        InterviewSessionService._broadcast_to_channel_layer(
            str(session.public_id),
            {"event": "TASK_OBSERVATION_STARTED", **cls._task_event_payload(session, task)},
        )
        return task

    @classmethod
    def current_task(cls, *, session):
        cls._ensure_task_observation_enabled(session)
        task = cls._get_current_or_next_task(session)
        if task is None:
            return None
        return task

    @classmethod
    def complete_task(
        cls,
        *,
        session,
        session_task,
        execution_time_seconds,
        observed_steps,
        review_required=False,
        review_reason="",
        integrity_flags=None,
        result_payload=None,
        actor=None,
    ):
        cls._ensure_task_observation_enabled(session)
        cls._ensure_session_active(session)
        if session_task.session_id != session.id:
            cls._log_invalid_transition(
                session=session,
                session_task=session_task,
                actor=actor,
                reason="Task does not belong to this session",
            )
            raise ValueError("Task does not belong to this session")

        if session_task.status in {
            SessionObservedTaskStatus.COMPLETED,
            SessionObservedTaskStatus.FAILED,
            SessionObservedTaskStatus.REQUIRES_REVIEW,
        }:
            cls._log_invalid_transition(
                session=session,
                session_task=session_task,
                actor=actor,
                reason=(
                    "Duplicate task completion attempt rejected because the task "
                    f"is already in terminal state {session_task.status}"
                ),
            )
            raise ValueError(
                f"Task completion has already been finalized with status {session_task.status}"
            )

        if session_task.status != SessionObservedTaskStatus.IN_PROGRESS:
            cls._log_invalid_transition(
                session=session,
                session_task=session_task,
                actor=actor,
                reason="Task must be in progress before completion",
            )
            raise ValueError("Task must be started before completion")

        observed_steps = observed_steps or []
        integrity_flags = integrity_flags or []
        result_payload = result_payload or {}
        expected_steps = session_task.task_definition.expected_steps or []
        missing_steps = [step for step in expected_steps if step not in observed_steps]
        task_completed = bool(expected_steps) and not missing_steps
        if not expected_steps:
            task_completed = bool(observed_steps)
        sequence_correct = cls._steps_match_order(expected_steps, observed_steps)

        duration_reason = ""
        if (
            session_task.task_definition.max_duration_seconds
            and execution_time_seconds > session_task.task_definition.max_duration_seconds
        ):
            duration_reason = (
                f"Task exceeded max duration of {session_task.task_definition.max_duration_seconds} seconds."
            )

        computed_review_reason = review_reason.strip()
        effective_review_required = bool(
            review_required
            or missing_steps
            or not sequence_correct
            or duration_reason
            or not task_completed
            or integrity_flags
        )
        if not computed_review_reason:
            reasons = []
            if missing_steps:
                reasons.append("Required steps were missing.")
            if not sequence_correct:
                reasons.append("Observed steps were out of sequence.")
            if duration_reason:
                reasons.append(duration_reason)
            if integrity_flags:
                reasons.append("Integrity flags were raised during task observation.")
            if not task_completed and not reasons:
                reasons.append("Task evidence was incomplete.")
            computed_review_reason = " ".join(reasons)

        result_status = TaskObservationResultStatus.COMPLETED
        session_status = SessionObservedTaskStatus.COMPLETED
        if effective_review_required:
            result_status = TaskObservationResultStatus.COMPLETED_WITH_REVIEW if task_completed else TaskObservationResultStatus.INCOMPLETE
            session_status = SessionObservedTaskStatus.REQUIRES_REVIEW

        with transaction.atomic():
            session_task.status = session_status
            session_task.completed_at = timezone.now()
            session_task.metadata = {
                **(session_task.metadata or {}),
                "last_execution_time_seconds": execution_time_seconds,
                "last_task_completed": task_completed,
                "last_sequence_correct": sequence_correct,
            }
            session_task.save(update_fields=["status", "completed_at", "metadata", "updated_at"])

            result, _ = TaskObservationResult.objects.update_or_create(
                session_task=session_task,
                defaults={
                    "session": session,
                    "candidate": session.candidate,
                    "status": result_status,
                    "task_completed": task_completed,
                    "sequence_correct": sequence_correct,
                    "execution_time_seconds": execution_time_seconds,
                    "review_required": effective_review_required,
                    "review_reason": computed_review_reason,
                    "observed_steps": observed_steps,
                    "missing_steps": missing_steps,
                    "integrity_flags": integrity_flags,
                    "result_payload": {
                        **result_payload,
                        "task_definition_code": session_task.task_definition.task_code,
                        "task_definition_name": session_task.task_definition.task_name,
                        "expected_steps": expected_steps,
                    },
                    "generated_by": actor if getattr(actor, "is_authenticated", False) else None,
                    "generated_at": timezone.now(),
                },
            )

        action = (
            AuditLogAction.TASK_OBSERVATION_REQUIRES_REVIEW
            if effective_review_required
            else AuditLogAction.TASK_OBSERVATION_COMPLETED
        )
        description = (
            f"Task observation requires review for task {session_task.task_definition.task_code}"
            if effective_review_required
            else f"Task observation completed for task {session_task.task_definition.task_code}"
        )
        access_context = "staff" if actor else "session_token"
        InterviewSessionService._log_voice_or_session_event(
            actor=actor,
            action=action,
            category=AuditLogCategory.SESSION,
            description=description,
            resource=session,
            data=cls._task_event_payload(
                session,
                session_task,
                result=result,
                extra={
                    "execution_time_seconds": execution_time_seconds,
                    "task_completed": task_completed,
                    "sequence_correct": sequence_correct,
                    "review_required": effective_review_required,
                    "review_reason": computed_review_reason,
                },
                access_context=access_context,
            ),
        )
        InterviewSessionService._broadcast_to_channel_layer(
            str(session.public_id),
            {
                "event": "TASK_OBSERVATION_COMPLETED",
                **cls._task_event_payload(session, session_task, result=result),
            },
        )
        return result

    @classmethod
    def list_results(cls, *, session):
        cls._ensure_task_observation_enabled(session)
        return session.task_observation_results.select_related("session_task", "session_task__task_definition").all()

    @classmethod
    def _get_current_or_next_task(cls, session):
        current = session.observed_tasks.select_related("task_definition").filter(
            status=SessionObservedTaskStatus.IN_PROGRESS
        ).order_by("task_order").first()
        if current:
            return current
        return session.observed_tasks.select_related("task_definition").filter(
            status__in=[SessionObservedTaskStatus.READY, SessionObservedTaskStatus.PENDING]
        ).order_by("task_order").first()

    @classmethod
    def _steps_match_order(cls, expected_steps, observed_steps):
        if not expected_steps:
            return bool(observed_steps)
        observed_relevant = [step for step in observed_steps if step in expected_steps]
        return observed_relevant == expected_steps[: len(observed_relevant)] and len(observed_relevant) == len(expected_steps)

    @classmethod
    def _ensure_task_observation_enabled(cls, session):
        if not session.task_observation_enabled:
            raise ValueError("Task observation is not enabled for this interview session")

    @classmethod
    def _ensure_session_active(cls, session):
        if session.is_closed():
            raise ValueError("Cannot process task observation on a closed session")
        if session.status != InterviewSessionStatus.IN_PROGRESS:
            raise ValueError("Session must be in progress")

    @classmethod
    def _log_invalid_transition(cls, *, session, session_task, actor, reason):
        access_context = "staff" if actor else "session_token"
        InterviewSessionService._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.TASK_OBSERVATION_INVALID_TRANSITION,
            category=AuditLogCategory.SESSION,
            description=reason,
            resource=session,
            severity=AuditLogSeverity.WARNING,
            data=cls._task_event_payload(session, session_task, extra={"reason": reason}, access_context=access_context),
        )

    @classmethod
    def _task_event_payload(cls, session, session_task, *, result=None, extra=None, access_context="staff"):
        payload = session_event_payload(
            session,
            {
                "access_context": access_context,
                "task_id": str(session_task.public_id),
                "task_definition_id": str(session_task.task_definition.public_id),
                "task_code": session_task.task_definition.task_code,
                "task_name": session_task.task_definition.task_name,
                "task_order": session_task.task_order,
                "task_status": session_task.status,
            },
        )
        if result is not None:
            payload["task_result_id"] = str(result.public_id)
            payload["task_result_status"] = result.status
        if extra:
            payload.update(extra)
        return payload


class InterviewVoicePipelineService:
    stt_service_class = SpeechToTextService
    tts_service_class = TextToSpeechService

    @classmethod
    @transaction.atomic
    def upload_response_audio(cls, *, session, question, uploaded_file, duration_seconds, actor=None):
        cls._validate_audio_file(uploaded_file, duration_seconds)
        cls._ensure_question_is_active(session, question)

        access_context = "staff" if actor else "session_token"
        InterviewSessionService._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.AUDIO_UPLOAD_STARTED,
            category=AuditLogCategory.VOICE,
            description=f"Audio upload started for question {question.question_order}",
            resource=session,
            data=voice_event_payload(
                session,
                question=question,
                extra={
                    "mime_type": uploaded_file.content_type or "",
                    "file_size_bytes": uploaded_file.size,
                    "duration_seconds": duration_seconds,
                },
                access_context=access_context,
            ),
        )

        attempt_number = question.responses.count() + 1
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type=CandidateResponseType.VOICE,
            duration_seconds=duration_seconds,
            attempt_number=attempt_number,
            audio_file=uploaded_file,
            audio_mime_type=uploaded_file.content_type or "",
            audio_file_size_bytes=uploaded_file.size or 0,
            audio_uploaded_at=timezone.now(),
            stt_status="PENDING",
            metadata={
                "upload_filename": getattr(uploaded_file, "name", ""),
            },
        )

        response.audio_url = MediaStorageService.resolve_url(response.audio_file.name)
        response.save(update_fields=["audio_url", "updated_at"])

        question.status = SessionQuestionStatus.ANSWERED
        question.answered_at = timezone.now()
        question.save(update_fields=["status", "answered_at", "updated_at"])

        session.last_activity_at = timezone.now()
        session.current_question_index = question.question_order
        session.save(update_fields=["last_activity_at", "current_question_index", "updated_at"])

        payload = voice_event_payload(
            session,
            question=question,
            response=response,
            extra={
                "storage_url": response.audio_url,
                "storage_key": response.audio_file.name,
                "mime_type": response.audio_mime_type,
                "file_size_bytes": response.audio_file_size_bytes,
                "duration_seconds": response.duration_seconds,
                "uploaded_at": response.audio_uploaded_at.isoformat() if response.audio_uploaded_at else "",
            },
            access_context=access_context,
        )
        InterviewSessionService._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.AUDIO_UPLOAD_COMPLETED,
            category=AuditLogCategory.VOICE,
            description=f"Audio upload completed for question {question.question_order}",
            resource=session,
            data=payload,
        )
        InterviewSessionService._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.RESPONSE_ATTACHED,
            category=AuditLogCategory.VOICE,
            description=f"Voice response attached to question {question.question_order}",
            resource=session,
            data=payload,
        )
        InterviewSessionService._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.ANSWER_SUBMITTED,
            category=AuditLogCategory.SESSION,
            description=f"Answer submitted for question {question.question_order}",
            resource=session,
            data=payload,
        )

        InterviewSessionSocketRegistry.broadcast_sync(
            str(session.public_id),
            {
                "event": "ANSWER_RECEIVED",
                **voice_event_payload(session, question=question, response=response),
            },
        )
        InterviewSessionService._broadcast_to_channel_layer(
            str(session.public_id),
            {
                "event": "ANSWER_RECEIVED",
                **voice_event_payload(session, question=question, response=response),
            },
        )
        return response

    @classmethod
    @transaction.atomic
    def transcribe_response(cls, *, session, response, actor=None, language_code=None):
        if response.session_id != session.id:
            raise ValueError("Response does not belong to this session")
        if response.response_type != CandidateResponseType.VOICE:
            raise ValueError("Only voice responses can be transcribed")
        if not response.audio_file:
            raise ValueError("Response has no uploaded audio")
        if response.stt_status == "COMPLETED" and response.original_transcript:
            return response
        if language_code and language_code not in LANGUAGE_CODE_MAP.values():
            raise ValueError(f"Unsupported answer language: {language_code}")
        # Candidate-selected language this specific audio answer was
        # recorded in, when given, overrides the session's fixed default -
        # used both as the speech-to-text hint below and (unless the
        # provider's own detection disagrees) the stored transcript_language.
        language_code = language_code or session.stt_language_code

        question = response.question
        access_context = "staff" if actor else "session_token"
        response.stt_status = "PROCESSING"
        response.stt_error_code = ""
        response.stt_error_message = ""
        response.save(update_fields=["stt_status", "stt_error_code", "stt_error_message", "updated_at"])

        InterviewSessionService._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.TRANSCRIPTION_REQUESTED,
            category=AuditLogCategory.VOICE,
            description=f"Transcription requested for response {response.public_id}",
            resource=session,
            data=voice_event_payload(session, question=question, response=response, access_context=access_context),
        )

        stt_service = cls.stt_service_class()
        try:
            with response.audio_file.open("rb") as audio_stream:
                transcript_result = stt_service.transcribe(
                    file_obj=audio_stream,
                    filename=response.audio_file.name.rsplit("/", 1)[-1],
                    mime_type=response.audio_mime_type or "application/octet-stream",
                    language_code=language_code,
                )
        except (VoiceProviderError, VoiceProviderConfigurationError) as exc:
            response.stt_status = "FAILED"
            response.stt_error_code = exc.code
            response.stt_error_message = str(exc)
            response.stt_metadata = {"error_metadata": exc.metadata}
            response.stt_processed_at = timezone.now()
            response.save(
                update_fields=[
                    "stt_status",
                    "stt_error_code",
                    "stt_error_message",
                    "stt_metadata",
                    "stt_processed_at",
                    "updated_at",
                ]
            )
            InterviewSessionService._log_voice_or_session_event(
                actor=actor,
                action=AuditLogAction.TRANSCRIPTION_FAILED,
                category=AuditLogCategory.VOICE,
                description=f"Transcription failed for response {response.public_id}",
                resource=session,
                severity=AuditLogSeverity.ERROR,
                data=voice_event_payload(
                    session,
                    question=question,
                    response=response,
                    extra={
                        "provider": stt_service.provider,
                        "error_code": response.stt_error_code,
                        "error_message": response.stt_error_message,
                    },
                    access_context=access_context,
                ),
            )
            return response

        response.transcript = transcript_result["transcript"]
        response.original_transcript = transcript_result["transcript"]
        response.text_response = response.text_response or transcript_result["transcript"]
        response.transcript_language = transcript_result["detected_language"] or language_code
        response.stt_provider = transcript_result["provider"]
        response.stt_model = transcript_result["provider_model"]
        response.stt_request_id = transcript_result["request_id"]
        response.stt_confidence = transcript_result["confidence"]
        response.stt_status = transcript_result["processing_status"]
        response.stt_error_code = ""
        response.stt_error_message = ""
        response.stt_metadata = transcript_result["metadata"]
        response.stt_processed_at = timezone.now()
        response.save(
            update_fields=[
                "transcript",
                "original_transcript",
                "text_response",
                "transcript_language",
                "stt_provider",
                "stt_model",
                "stt_request_id",
                "stt_confidence",
                "stt_status",
                "stt_error_code",
                "stt_error_message",
                "stt_metadata",
                "stt_processed_at",
                "updated_at",
            ]
        )

        InterviewSessionService._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.TRANSCRIPTION_COMPLETED,
            category=AuditLogCategory.VOICE,
            description=f"Transcription completed for response {response.public_id}",
            resource=session,
            data=voice_event_payload(
                session,
                question=question,
                response=response,
                extra={
                    "provider": response.stt_provider,
                    "provider_model": response.stt_model,
                    "request_id": response.stt_request_id,
                    "detected_language": response.transcript_language,
                    "confidence": str(response.stt_confidence) if response.stt_confidence is not None else None,
                    "processing_status": response.stt_status,
                },
                access_context=access_context,
            ),
        )
        if response.stt_status == "COMPLETED" and response.original_transcript:
            from api.translation.services import AIProcessingOrchestrationService

            AIProcessingOrchestrationService.auto_process_response_ai(
                response=response,
                actor=actor,
                target_override=session.translation_target,
            )
        return response

    @classmethod
    @transaction.atomic
    def get_or_generate_question_audio(cls, *, session, actor=None, language_code=None):
        question, completed = InterviewSessionService.get_or_activate_current_question(session, actor=actor)
        if completed or question is None:
            raise ValueError("Session is already completed")

        tts_service = cls.tts_service_class()
        # Candidate-selected read-aloud language, when given, overrides the
        # session's default (set once at creation from candidate_language) -
        # only for this playback; doesn't change the session's own record.
        if language_code and language_code not in LANGUAGE_CODE_MAP.values():
            raise ValueError(f"Unsupported read-aloud language: {language_code}")
        language_code = language_code or session.tts_language_code or "en-US"
        # No fallback to the English voice name here - a mismatched
        # language/voice pair sent to the provider risks it reading the
        # text with the wrong accent, or in the wrong language entirely.
        # Omitting the name for a language we don't have a specific voice
        # mapped for lets the provider pick an appropriate default voice
        # for that language on its own.
        voice_name = (getattr(settings, "TTS_VOICE_MAP", {}) or {}).get(language_code, "")
        existing = QuestionAudioArtifact.objects.filter(
            session=session,
            question=question,
            language_code=language_code,
            voice_name=voice_name,
        ).first()
        if existing and existing.audio_file:
            return existing

        # question.question_text is always authored in English - previously
        # this got sent straight to the TTS provider under whatever
        # language_code was requested, which doesn't translate anything: the
        # provider just reads the English words using that language's voice/
        # phoneme model, producing English content in a foreign accent
        # rather than actual speech in that language. Translate first for
        # any non-English target.
        text_to_speak = question.question_text
        if not language_code.startswith("en"):
            try:
                translation = TranslationService.translate(
                    text=question.question_text,
                    source_language="en",
                    target_language=language_code.split("-")[0],
                )
                text_to_speak = translation["translated_text"] or question.question_text
            except AIProcessingError:
                # Fall back to the original English text rather than failing
                # the whole request - reading it in English is still more
                # useful than no audio at all, and the candidate can read
                # the on-screen text either way.
                text_to_speak = question.question_text

        try:
            synthesis = tts_service.synthesize(text=text_to_speak, language_code=language_code)
        except (VoiceProviderError, VoiceProviderConfigurationError) as exc:
            InterviewSessionService._log_voice_or_session_event(
                actor=actor,
                action=AuditLogAction.QUESTION_AUDIO_GENERATED,
                category=AuditLogCategory.VOICE,
                description=f"Question audio generation failed for question {question.question_order}",
                resource=session,
                severity=AuditLogSeverity.ERROR,
                data=voice_event_payload(
                    session,
                    question=question,
                    extra={
                        "provider": tts_service.provider,
                        "error_code": exc.code,
                        "error_message": str(exc),
                    },
                ),
            )
            raise ValueError(str(exc)) from exc

        artifact = QuestionAudioArtifact.objects.create(
            session=session,
            question=question,
            provider=synthesis["provider"],
            voice_name=synthesis["voice_name"],
            language_code=synthesis["language_code"],
            mime_type=synthesis["mime_type"],
            duration_estimate_seconds=synthesis["duration_estimate_seconds"],
            metadata=synthesis["metadata"],
        )
        filename_extension = cls._extension_for_mime_type(synthesis["mime_type"])
        target_path = f"interviews/sessions/{session.id}/questions/{question.id}/tts/{artifact.public_id}{filename_extension}"
        stored = MediaStorageService.save_bytes(content=synthesis["audio_bytes"], target_path=target_path)
        artifact.audio_file.name = stored["storage_key"]
        artifact.audio_url = stored["storage_url"]
        artifact.file_size_bytes = stored["file_size_bytes"] or len(synthesis["audio_bytes"])
        artifact.save(update_fields=["audio_file", "audio_url", "file_size_bytes", "updated_at"])

        InterviewSessionService._log_voice_or_session_event(
            actor=actor,
            action=AuditLogAction.QUESTION_AUDIO_GENERATED,
            category=AuditLogCategory.VOICE,
            description=f"Question audio generated for question {question.question_order}",
            resource=session,
            data=voice_event_payload(
                session,
                question=question,
                extra={
                    "provider": artifact.provider,
                    "voice_name": artifact.voice_name,
                    "language_code": artifact.language_code,
                    "audio_url": artifact.audio_url,
                    "storage_key": artifact.audio_file.name,
                    "duration_estimate_seconds": artifact.duration_estimate_seconds,
                    "generated_at": artifact.generated_at.isoformat(),
                },
            ),
        )
        return artifact

    @classmethod
    def _validate_audio_file(cls, uploaded_file, duration_seconds):
        mime_type = getattr(uploaded_file, "content_type", "") or ""
        if mime_type not in settings.INTERVIEW_AUDIO_ALLOWED_MIME_TYPES:
            raise ValueError("Unsupported audio type")
        if uploaded_file.size > settings.INTERVIEW_AUDIO_MAX_FILE_SIZE_BYTES:
            raise ValueError("Audio file is too large")
        if duration_seconds > settings.INTERVIEW_AUDIO_MAX_DURATION_SECONDS:
            raise ValueError("Audio duration exceeds the allowed limit")

    @classmethod
    def _ensure_question_is_active(cls, session, question):
        if session.is_closed():
            raise ValueError("Cannot answer after session is closed")
        if session.status != InterviewSessionStatus.IN_PROGRESS:
            raise ValueError("Session must be in progress")
        if question.session_id != session.id:
            raise ValueError("Question does not belong to this session")
        if question.status != SessionQuestionStatus.ASKED:
            raise ValueError("Question is not currently active")

    @classmethod
    def _extension_for_mime_type(cls, mime_type):
        mapping = {
            "audio/mpeg": ".mp3",
            "audio/wav": ".wav",
            "audio/ogg": ".ogg",
        }
        return mapping.get(mime_type, ".bin")
