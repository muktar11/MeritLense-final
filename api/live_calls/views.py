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
from api.sessions.services import InterviewSessionService
from api.translation.services import TranslationService

from .auth import OptionalJWTAuthentication, issue_socket_ticket
from .models import LiveCallParticipant, LiveCallSession
from .serializers import LanguagePreferencesSerializer, LiveCallJoinSerializer, LiveCallSerializer


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
        if not audio:
            raise ValidationError({"detail": "An audio recording is required."})

        source_language = (participant.input_language or session.candidate_language or "en-US").strip()
        target_language = (peer.output_language or participant.output_language or "en-US").strip()

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

        if not detected_language_matches(source_language, transcript_payload.get("detected_language")):
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
            "sent_at": timezone.now().isoformat(),
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
