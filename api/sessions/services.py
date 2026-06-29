import random

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
    SessionQuestionStatus,
)
from api.interviews.voice_services import (
    SpeechToTextService,
    TextToSpeechService,
    VoiceProviderConfigurationError,
    VoiceProviderError,
)
from api.questions.models import QuestionTemplate
from api.storage.services import MediaStorageService

from .models import CandidateResponse, IntegrityLog, InterviewSession, QuestionAudioArtifact, SessionQuestion
from .realtime import InterviewSessionSocketRegistry


LANGUAGE_CODE_MAP = {
    "EN": "en-US",
    "ES": "es-ES",
    "FR": "fr-FR",
    "AR": "ar-SA",
    "DE": "de-DE",
    "ZH": "zh-CN",
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

        selected_templates = cls._mix_difficulties(localized, target_count)
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
    def _mix_difficulties(cls, templates, target_count):
        by_difficulty = {"EASY": [], "MEDIUM": [], "HARD": [], "OTHER": []}
        for template in templates:
            by_difficulty.get(template.difficulty, by_difficulty["OTHER"]).append(template)

        for items in by_difficulty.values():
            random.shuffle(items)

        ordered = []
        while len(ordered) < target_count and any(by_difficulty.values()):
            for key in ("EASY", "MEDIUM", "HARD", "OTHER"):
                if by_difficulty[key] and len(ordered) < target_count:
                    ordered.append(by_difficulty[key].pop())

        return ordered


class InterviewSessionService:
    @classmethod
    @transaction.atomic
    def create_session(cls, *, candidate, config, created_by):
        language = candidate.preferred_language or config.language or "EN"
        session = InterviewSession.objects.create(
            candidate=candidate,
            organization=candidate.company,
            config=config,
            role_name=config.role_name or candidate.get_job_role_display(),
            role_code=config.role_code,
            ui_language=config.language or language,
            candidate_language=language,
            tts_language_code=LANGUAGE_CODE_MAP.get(language, "en-US"),
            stt_language_code=LANGUAGE_CODE_MAP.get(language, "en-US"),
            translation_target=config.language if config.enable_translation else "",
            total_questions=config.total_questions,
            evaluation_tier=config.evaluation_tier,
            rubric_version=config.rubric_version,
            question_set_version=config.question_set_version,
            expires_at=InterviewSession.build_expiry(config.duration_minutes),
            created_by=created_by,
        )
        QuestionGenerationService.generate_questions(session)
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
    def start_session(cls, session, actor=None):
        if session.is_expired():
            session.status = InterviewSessionStatus.EXPIRED
            session.save(update_fields=["status", "updated_at"])
            raise ValueError("Cannot start expired session")
        if session.status == InterviewSessionStatus.COMPLETED:
            raise ValueError("Cannot restart completed session")

        events = []
        if session.status == InterviewSessionStatus.CREATED:
            session.verification_status = IdentityVerificationStatus.PENDING
            session.status = InterviewSessionStatus.VERIFICATION_PENDING
            session.save(update_fields=["status", "verification_status", "updated_at"])
            events.append(("SESSION_VERIFICATION_PENDING", AuditLogAction.SESSION_VERIFICATION_PENDING))

            session.verification_status = IdentityVerificationStatus.VERIFIED
            session.identity_verified = True
            session.single_face_detected = True
            session.status = InterviewSessionStatus.READY
            session.save(
                update_fields=[
                    "status",
                    "verification_status",
                    "identity_verified",
                    "single_face_detected",
                    "updated_at",
                ]
            )
            events.append(("SESSION_READY", AuditLogAction.SESSION_READY))

        session.start()
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

        attempt_number = question.responses.count() + 1
        response = CandidateResponse.objects.create(
            session=session,
            question=question,
            response_type=response_type,
            transcript=transcript,
            text_response=text_response or transcript,
            duration_seconds=duration_seconds,
            attempt_number=attempt_number,
            metadata=metadata or {},
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
        return response

    @classmethod
    @transaction.atomic
    def complete_session(cls, session, actor=None):
        session.complete()
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
    def transcribe_response(cls, *, session, response, actor=None):
        if response.session_id != session.id:
            raise ValueError("Response does not belong to this session")
        if response.response_type != CandidateResponseType.VOICE:
            raise ValueError("Only voice responses can be transcribed")
        if not response.audio_file:
            raise ValueError("Response has no uploaded audio")
        if response.stt_status == "COMPLETED" and response.original_transcript:
            return response

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
                    language_code=session.stt_language_code,
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
        response.transcript_language = transcript_result["detected_language"] or session.stt_language_code
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
        return response

    @classmethod
    @transaction.atomic
    def get_or_generate_question_audio(cls, *, session, actor=None):
        question, completed = InterviewSessionService.get_or_activate_current_question(session, actor=actor)
        if completed or question is None:
            raise ValueError("Session is already completed")

        tts_service = cls.tts_service_class()
        language_code = session.tts_language_code or "en-US"
        voice_name = (getattr(settings, "TTS_VOICE_MAP", {}) or {}).get(language_code) or "en-US-Standard-C"
        existing = QuestionAudioArtifact.objects.filter(
            session=session,
            question=question,
            language_code=language_code,
            voice_name=voice_name,
        ).first()
        if existing and existing.audio_file:
            return existing

        try:
            synthesis = tts_service.synthesize(text=question.question_text, language_code=language_code)
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
