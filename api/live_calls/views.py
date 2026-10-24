from datetime import timedelta
import base64
import hashlib
import hmac
import time
import uuid

from django.conf import settings
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.generics import GenericAPIView

from api.core.public_ids import build_object_identifier_filter
from api.interviews.voice_services import (
    SpeechToTextService,
    TextToSpeechService,
    VoiceProviderError,
    detected_language_matches,
)
from api.sessions.models import InterviewSession
from api.sessions.services import InterviewSessionService, QuestionGenerationService
from api.translation.services import TranslationService

from api.evaluations.evaluator_rating_services import SCORECARD_FIELDS, scorecard_field_for

from .auth import OptionalJWTAuthentication, issue_socket_ticket
from .models import LiveCallParticipant, LiveCallSegment, LiveCallSession
from .serializers import (
    LanguagePreferencesSerializer, LiveCallJoinSerializer, LiveCallSegmentSerializer, LiveCallSerializer,
)

MAX_TYPED_SEGMENT_LENGTH = 2000


def _segment_competency(call, role, requested):
    """The evaluator tags a turn with the competency open in the interview
    guide; a candidate turn answers the most recent evaluator turn."""
    if role == LiveCallParticipant.ROLE_EVALUATOR:
        if requested in (None, ""):
            return ""
        if requested not in SCORECARD_FIELDS:
            raise ValidationError({"competency": "Unknown competency."})
        return requested
    last_question = (
        call.segments.filter(speaker_role=LiveCallParticipant.ROLE_EVALUATOR)
        .order_by("-sent_at", "-id").values_list("competency", flat=True).first()
    )
    return last_question or ""


def _session(identifier):
    try:
        lookup = build_object_identifier_filter(identifier)
    except ValueError as exc:
        raise ValidationError({"id": str(exc)}) from exc
    return get_object_or_404(
        InterviewSession.objects.select_related("linked_evaluation", "created_by"), **lookup
    )


def _role_for_request(session, request):
    if request.user.is_authenticated and session.can_manage(request.user):
        return LiveCallParticipant.ROLE_EVALUATOR
    token = request.headers.get("X-Session-Token") or request.query_params.get("token") or request.data.get("token")
    if session.token_is_valid(token):
        return LiveCallParticipant.ROLE_CANDIDATE
    raise PermissionDenied("You do not have access to this live call")


def _ice_servers(call):
    servers = []
    if settings.WEBRTC_STUN_URLS:
        servers.append({"urls": settings.WEBRTC_STUN_URLS})
    if settings.WEBRTC_TURN_URLS:
        username = settings.WEBRTC_TURN_USERNAME
        credential = settings.WEBRTC_TURN_CREDENTIAL
        if settings.WEBRTC_TURN_SECRET:
            expires = int(time.time()) + settings.WEBRTC_TURN_CREDENTIAL_TTL_SECONDS
            username = f"{expires}:{call.public_id}"
            credential = base64.b64encode(
                hmac.new(
                    settings.WEBRTC_TURN_SECRET.encode(), username.encode(), hashlib.sha1
                ).digest()
            ).decode()
        servers.append({
            "urls": settings.WEBRTC_TURN_URLS,
            "username": username,
            "credential": credential,
        })
    return servers


class LiveCallJoinView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = [OptionalJWTAuthentication]
    serializer_class = LiveCallJoinSerializer

    def post(self, request, session_id):
        session = _session(session_id)
        role = _role_for_request(session, request)
        if session.is_closed():
            raise ValidationError({"detail": "This interview session is closed"})
        early = timedelta(minutes=settings.LIVE_CALL_EARLY_JOIN_MINUTES)
        if session.scheduled_start_at and timezone.now() < session.scheduled_start_at - early:
            raise PermissionDenied({
                "detail": "The live call is not open yet",
                "scheduled_start_at": session.scheduled_start_at.isoformat(),
            })
        evaluation = getattr(session, "linked_evaluation", None)
        call, _ = LiveCallSession.objects.get_or_create(
            interview_session=session, defaults={"evaluation": evaluation}
        )
        if evaluation and call.evaluation_id != evaluation.id:
            call.evaluation = evaluation
            call.save(update_fields=["evaluation", "updated_at"])
        if call.state == LiveCallSession.STATE_ENDED:
            raise ValidationError({"detail": "This live call has ended"})
        participant, _ = LiveCallParticipant.objects.get_or_create(
            call=call,
            role=role,
            defaults={"user": request.user if request.user.is_authenticated else None},
        )
        if role == LiveCallParticipant.ROLE_EVALUATOR and participant.user_id != request.user.id:
            participant.user = request.user
            participant.save(update_fields=["user", "updated_at"])
        return Response({
            "call": LiveCallSerializer(call).data,
            "role": role,
            "websocket_ticket": issue_socket_ticket(call, role),
            "websocket_path": f"/ws/live-calls/{call.public_id}/",
            "ice_servers": _ice_servers(call),
            "media": {
                "input_format": "pcm_s16le;rate=16000;channels=1",
                "translated_audio_event": "translated_audio",
                "original_remote_audio_muted": True,
            },
        })


class LiveCallPreferencesView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = [OptionalJWTAuthentication]
    serializer_class = LanguagePreferencesSerializer

    def _objects(self, request, session_id):
        session = _session(session_id)
        role = _role_for_request(session, request)
        call = get_object_or_404(LiveCallSession, interview_session=session)
        participant = get_object_or_404(LiveCallParticipant, call=call, role=role)
        return call, participant

    def get(self, request, session_id):
        call, _ = self._objects(request, session_id)
        return Response(LiveCallSerializer(call).data)

    def put(self, request, session_id):
        call, participant = self._objects(request, session_id)
        serializer = LanguagePreferencesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        participant.input_language = serializer.validated_data["input_language"]
        participant.output_language = serializer.validated_data["output_language"]
        participant.save(update_fields=["input_language", "output_language", "updated_at"])
        async_to_sync(get_channel_layer().group_send)(f"live_call_{call.public_id}", {
            "type": "preferences.changed", "role": participant.role
        })
        return Response(LiveCallSerializer(call).data)


class LiveCallSegmentView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = [OptionalJWTAuthentication]

    def post(self, request, session_id):
        session = _session(session_id)
        role = _role_for_request(session, request)
        call = get_object_or_404(LiveCallSession, interview_session=session)
        participant = get_object_or_404(LiveCallParticipant, call=call, role=role)
        peer = call.participants.exclude(role=role).first()
        if not peer:
            raise ValidationError({"detail": "The other participant is not available yet."})

        audio = request.FILES.get("audio") or request.data.get("audio")
        typed_text = request.data.get("text")
        typed_text = typed_text.strip() if isinstance(typed_text, str) else ""
        if not audio and not typed_text:
            raise ValidationError({"detail": "An audio recording or a typed message is required."})
        if len(typed_text) > MAX_TYPED_SEGMENT_LENGTH:
            raise ValidationError({"detail": f"A typed message can be at most {MAX_TYPED_SEGMENT_LENGTH} characters."})

        source_language = (participant.input_language or session.candidate_language or "en-US").strip()
        target_language = (peer.output_language or participant.output_language or "en-US").strip()

        if audio:
            stt = SpeechToTextService()
            try:
                transcript_payload = stt.transcribe(
                    file_obj=audio,
                    filename=getattr(audio, "name", "segment.webm"),
                    mime_type=getattr(audio, "content_type", "audio/webm"),
                    language_code=source_language,
                )
            except VoiceProviderError as exc:
                raise ValidationError({"detail": "Transcription failed. Please try again."}) from exc
            original_text = (transcript_payload.get("transcript") or "").strip()
            if not original_text:
                raise ValidationError({"detail": "No speech was detected in the recording."})

            # detected_language_matches() encodes Whisper's specific naming
            # quirks - meaningless for an Azure-routed result, which either
            # succeeds as the requested language or comes back empty (already
            # caught by the check above).
            if transcript_payload.get("provider") == "OPENAI" and not detected_language_matches(
                source_language, transcript_payload.get("detected_language")
            ):
                raise ValidationError(
                    {
                        "detail": (
                            "We couldn't reliably transcribe that in the selected language. This can happen "
                            "with languages our speech-to-text provider doesn't fully support yet. Please try "
                            "speaking again, or switch to a different language."
                        ),
                        "code": "stt_language_mismatch",
                    }
                )
        else:
            # A typed turn goes through the same translation, voice and
            # scoring path as a spoken one; only transcription is skipped.
            original_text = typed_text

        competency = _segment_competency(call, role, request.data.get("competency"))
        segment_id = str(uuid.uuid4())
        if role == LiveCallParticipant.ROLE_CANDIDATE:
            # Live-call transcripts used to exist only in the WebSocket
            # payload. Consequently the rule engine had no CandidateResponse
            # rows to score and Consistency incorrectly fell back to zero.
            # Attach each candidate turn to the next active fixed-pool
            # question so it follows the same AI/rule pipeline as an ordinary
            # interview response.
            try:
                question, completed = InterviewSessionService.get_or_activate_current_question(
                    session,
                    actor=None,
                )
                if question is not None and not completed:
                    InterviewSessionService.submit_response(
                        session,
                        question,
                        transcript=original_text,
                        text_response=original_text,
                        language_code=source_language,
                        metadata={
                            "source": "live_call",
                            "live_call_segment_id": segment_id,
                            "input_mode": "AUDIO" if audio else "TEXT",
                        },
                    )
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)}) from exc

        try:
            translation = TranslationService.translate(
                text=original_text,
                source_language=source_language.split("-", 1)[0].lower(),
                target_language=target_language.split("-", 1)[0].lower(),
            )
            translated_text = (translation.get("translated_text") or "").strip() or original_text
        except Exception:
            translated_text = original_text

        try:
            tts = TextToSpeechService()
            audio_payload = tts.synthesize(text=translated_text, language_code=target_language)
            translated_audio = base64.b64encode(audio_payload["audio_bytes"]).decode("ascii")
            mime_type = audio_payload.get("mime_type", "audio/mpeg")
        except Exception:
            translated_audio = ""
            mime_type = "audio/mpeg"

        sent_at = timezone.now()
        LiveCallSegment.objects.create(
            call=call,
            segment_id=segment_id,
            speaker_role=role,
            input_mode=LiveCallSegment.INPUT_AUDIO if audio else LiveCallSegment.INPUT_TEXT,
            source_language=source_language,
            target_language=target_language,
            original_text=original_text,
            translated_text=translated_text,
            competency=competency,
            sent_at=sent_at,
        )
        payload = {
            "event": "translation_segment",
            "id": segment_id,
            "speaker_role": role,
            "recipient_role": peer.role,
            "source_language": source_language,
            "target_language": target_language,
            "original_text": original_text,
            "translated_text": translated_text,
            "translated_audio": translated_audio,
            "mime_type": mime_type,
            "input_mode": LiveCallSegment.INPUT_AUDIO if audio else LiveCallSegment.INPUT_TEXT,
            "competency": competency,
            "sent_at": sent_at.isoformat(),
        }

        async_to_sync(get_channel_layer().group_send)(f"live_call_{call.public_id}", {
            "type": "translation.segment",
            "payload": payload,
            "recipient_role": peer.role,
            "sender_role": role,
        })

        return Response({
            "status": "translated",
            "segment": payload,
        })


class LiveCallConversationView(GenericAPIView):
    """The saved conversation of a call, oldest first, for either participant."""

    permission_classes = [AllowAny]
    authentication_classes = [OptionalJWTAuthentication]

    def get(self, request, session_id):
        session = _session(session_id)
        _role_for_request(session, request)
        call = LiveCallSession.objects.filter(interview_session=session).first()
        segments = call.segments.all() if call else LiveCallSegment.objects.none()
        return Response(LiveCallSegmentSerializer(segments, many=True).data)


class LiveCallGuideView(GenericAPIView):
    """Evaluator-only context for the scheduled interview screen and the
    scorecard: candidate, role, the session's questions grouped by scorecard
    competency (suggested questions; the evaluator may ask their own, SCH-01)
    and the saved conversation."""

    permission_classes = [AllowAny]
    authentication_classes = [OptionalJWTAuthentication]

    def get(self, request, session_id):
        session = _session(session_id)
        if _role_for_request(session, request) != LiveCallParticipant.ROLE_EVALUATOR:
            raise PermissionDenied("Only the evaluator can open the interview guide.")
        questions = list(session.questions.select_related("question_template").order_by("question_order"))
        if not questions and not session.is_closed():
            try:
                questions = list(QuestionGenerationService.generate_questions(session))
            except Exception:  # a selection gap is audited by the service; the guide still opens
                questions = []
        grouped = {field: [] for field in SCORECARD_FIELDS}
        for question in questions:
            template = question.question_template
            field = scorecard_field_for(
                question.skill, getattr(template, "skill_tag", ""), getattr(template, "competency_code", ""),
                question.domain,
            )
            if field:
                grouped[field].append({
                    "id": str(question.public_id),
                    "text": question.question_text,
                    "order": question.question_order,
                    "status": question.status,
                })
        call = LiveCallSession.objects.filter(interview_session=session).first()
        evaluation = getattr(session, "linked_evaluation", None)
        candidate = session.candidate
        photo = candidate.verification_photo or candidate.profile_photo
        return Response({
            "session_id": str(session.public_id),
            "evaluation_id": str(evaluation.public_id) if evaluation else None,
            "candidate": {
                "name": candidate.get_full_name(),
                "photo_url": request.build_absolute_uri(photo.url) if photo else None,
            },
            "role_name": session.role_name,
            "role_code": session.role_code,
            "scheduled_start_at": session.scheduled_start_at,
            "evaluator_name": request.user.get_full_name(),
            "candidate_language": session.candidate_language,
            "call": LiveCallSerializer(call).data if call else None,
            "competencies": [{"key": field, "questions": grouped[field]} for field in SCORECARD_FIELDS],
            "conversation": LiveCallSegmentSerializer(call.segments.all() if call else [], many=True).data,
        })
