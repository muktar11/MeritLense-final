import random

from django.db import transaction
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from api.audit.services import AuditLogService
from api.core.constants import (
    AuditLogAction,
    AuditLogCategory,
    CandidateResponseType,
    IdentityVerificationStatus,
    InterviewSessionStatus,
    SessionQuestionStatus,
)
from api.questions.models import QuestionTemplate

from .models import CandidateResponse, IntegrityLog, InterviewSession, SessionQuestion


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


class QuestionGenerationService:
    @classmethod
    def generate_questions(cls, session):
        if session.questions.exists():
            return list(session.questions.order_by("question_order"))

        target_count = session.config.total_questions
        queryset = QuestionTemplate.objects.filter(
            is_active=True,
            role_name__iexact=session.role_name,
        )
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
                    skill=template.skill,
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
            ui_language=config.language or language,
            candidate_language=language,
            tts_language_code=LANGUAGE_CODE_MAP.get(language, "en-US"),
            stt_language_code=LANGUAGE_CODE_MAP.get(language, "en-US"),
            translation_target=config.language if config.enable_translation else "",
            total_questions=config.total_questions,
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

        if actor:
            AuditLogService.log(
                user=actor,
                action=AuditLogAction.QUESTION_SENT,
                category=AuditLogCategory.QUESTION,
                description=f"Question {next_question.question_order} sent for session {session.public_id}",
                resource=session,
                data=session_event_payload(
                    session,
                    {
                        "question_id": str(next_question.public_id),
                        "question_order": next_question.question_order,
                    },
                ),
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
