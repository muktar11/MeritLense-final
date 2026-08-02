from datetime import timedelta
import base64
import hashlib
import hmac
import time

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
from api.sessions.models import InterviewSession

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
            raise PermissionDenied("The live call is not open yet")
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
