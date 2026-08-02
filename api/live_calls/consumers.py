import base64
import json
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from .auth import read_socket_ticket
from .models import LiveCallParticipant, LiveCallSession
from .translation import AzureSpeechPipeline
from .services import update_participant_presence


class LiveCallConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.call_id = self.scope["url_route"]["kwargs"]["call_id"]
        # scope["query_string"] is the raw, still-percent-encoded query
        # string per the ASGI spec - a naive split("=") leaves the ticket's
        # signing.dumps() colons as literal "%3A", which never matches the
        # real signed value and made every connection fail auth.
        params = {k: v[0] for k, v in parse_qs(self.scope["query_string"].decode()).items()}
        try:
            claims = read_socket_ticket(params.get("ticket", ""), settings.LIVE_CALL_TICKET_TTL_SECONDS)
        except Exception:
            await self.close(code=4403)
            return
        if claims.get("call") != self.call_id:
            await self.close(code=4403)
            return
        self.role = claims.get("role")
        self.group_name = f"live_call_{self.call_id}"
        self.participant = await self._participant()
        if not self.participant:
            await self.close(code=4403)
            return
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self._set_connected(True)
        await self.channel_layer.group_send(self.group_name, {
            "type": "peer.presence", "sender": self.channel_name, "role": self.role, "connected": True
        })
        await self.send_json({"event": "ready", "role": self.role})
        peer_status = await self._peer_status()
        if peer_status is not None:
            # peer.presence broadcasts only fire reactively, at the moment
            # the OTHER side connects/disconnects - a client joining after
            # the peer is already connected would otherwise never learn
            # that, and get stuck showing "waiting for the other
            # participant" indefinitely (and, for the evaluator, never
            # trigger the offer-on-peer-connect rule below in
            # peer_presence()) even though the peer has been there the
            # whole time. Tell the newly-connecting client the peer's
            # actual current state directly, instead of only ever hearing
            # about *future* changes to it.
            peer_role, peer_connected = peer_status
            # Frontend's existing peer_presence handler (useLiveCall.ts)
            # already creates the offer on this exact event shape when
            # role == CANDIDATE/connected and the local role is EVALUATOR -
            # no separate signal needed here.
            await self.send_json({"event": "peer_presence", "role": peer_role, "connected": peer_connected})
        self.pipeline = None
        try:
            preferences = await self._translation_preferences()
            if preferences:
                self.pipeline = await sync_to_async(AzureSpeechPipeline, thread_sensitive=False)(
                    self.channel_layer, self.group_name, self.role,
                    preferences["input_language"], preferences["target_language"],
                )
        except Exception as exc:
            # Anything here (missing Azure Speech config, a Redis/DB blip on
            # the preferences lookup, ...) must not escape connect() - once
            # accept()/_set_connected(True) have already run, an unhandled
            # exception kills the socket without ever calling disconnect(),
            # which both drops the call and leaves the participant stuck
            # showing "connected" in the DB forever. Degrade to no
            # translation instead of taking the whole call down with it.
            await self.send_json({"event": "translation_unavailable", "detail": str(exc)})

    async def disconnect(self, close_code):
        if getattr(self, "pipeline", None):
            await sync_to_async(self.pipeline.close, thread_sensitive=False)()
        if hasattr(self, "group_name"):
            await self._set_connected(False)
            await self.channel_layer.group_send(self.group_name, {
                "type": "peer.presence", "sender": self.channel_name, "role": self.role, "connected": False
            })
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data is not None:
            if len(bytes_data) > settings.LIVE_CALL_MAX_AUDIO_FRAME_BYTES or len(bytes_data) % 2:
                await self.send_json({"event": "error", "detail": "Invalid PCM audio frame"})
                return
            if self.pipeline:
                self.pipeline.write(bytes_data)
            return
        try:
            message = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            await self.send_json({"event": "error", "detail": "Invalid JSON"})
            return
        action = message.get("action")
        if action == "ping":
            await self.send_json({"event": "pong"})
        elif action in {"offer", "answer", "ice_candidate", "renegotiate"}:
            await self.channel_layer.group_send(self.group_name, {
                "type": "signal.message", "sender": self.channel_name, "sender_role": self.role,
                "payload": {"event": action, "data": message.get("data")},
            })
        elif action == "end_call":
            await self._end_call()
            await self.channel_layer.group_send(self.group_name, {"type": "call.ended"})
        else:
            await self.send_json({"event": "error", "detail": "Unsupported action"})

    async def signal_message(self, event):
        if event["sender"] != self.channel_name:
            await self.send_json({**event["payload"], "from": event["sender_role"]})

    async def peer_presence(self, event):
        if event["sender"] != self.channel_name:
            await self.send_json({"event": "peer_presence", "role": event["role"], "connected": event["connected"]})
            if event["connected"] and self.pipeline is None:
                try:
                    preferences = await self._translation_preferences()
                    if preferences:
                        self.pipeline = await sync_to_async(AzureSpeechPipeline, thread_sensitive=False)(
                            self.channel_layer, self.group_name, self.role,
                            preferences["input_language"], preferences["target_language"],
                        )
                except Exception as exc:
                    # Same reasoning as connect(): this runs inside a
                    # group-broadcast handler, so an uncaught exception here
                    # kills THIS participant's own socket in reaction to the
                    # peer joining - not just theirs.
                    await self.send_json({"event": "translation_unavailable", "detail": str(exc)})

    async def translation_audio(self, event):
        if event["sender_role"] != self.role:
            await self.send_json({
                "event": "translated_audio", "mime_type": "audio/mpeg",
                "audio": base64.b64encode(event["audio"]).decode("ascii"),
            })

    async def translation_error(self, event):
        if event["sender_role"] == self.role:
            await self.send_json({"event": "translation_error", "detail": event["detail"]})

    async def call_ended(self, event):
        await self.send_json({"event": "call_ended"})

    async def preferences_changed(self, event):
        # Either speaker's input or listener's output changes this direction.
        if self.pipeline:
            await sync_to_async(self.pipeline.close, thread_sensitive=False)()
            self.pipeline = None
        preferences = await self._translation_preferences()
        if preferences:
            try:
                self.pipeline = await sync_to_async(AzureSpeechPipeline, thread_sensitive=False)(
                    self.channel_layer, self.group_name, self.role,
                    preferences["input_language"], preferences["target_language"],
                )
                await self.send_json({"event": "translation_reconfigured"})
            except Exception as exc:
                await self.send_json({"event": "translation_unavailable", "detail": str(exc)})

    async def send_json(self, payload):
        await self.send(text_data=json.dumps(payload))

    @database_sync_to_async
    def _participant(self):
        return LiveCallParticipant.objects.filter(call__public_id=self.call_id, role=self.role).first()

    @database_sync_to_async
    def _peer_status(self):
        peer = LiveCallParticipant.objects.filter(call_id=self.participant.call_id).exclude(role=self.role).first()
        if not peer:
            return None
        return (peer.role, peer.connected)

    @database_sync_to_async
    def _translation_preferences(self):
        speaker = LiveCallParticipant.objects.get(pk=self.participant.pk)
        peer = LiveCallParticipant.objects.filter(call_id=speaker.call_id).exclude(role=self.role).first()
        if not peer:
            return None
        return {"input_language": speaker.input_language, "target_language": peer.output_language}

    @database_sync_to_async
    def _set_connected(self, connected):
        update_participant_presence(self.participant.pk, connected)

    @database_sync_to_async
    def _end_call(self):
        LiveCallSession.objects.filter(pk=self.participant.call_id).update(
            state=LiveCallSession.STATE_ENDED, ended_at=timezone.now()
        )
