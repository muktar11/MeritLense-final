import base64
import json
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.utils import timezone

from .auth import read_socket_ticket
from .models import LiveCallParticipant, LiveCallSession
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
        await self.send_json({"event": "ready", "role": self.role})

        if self.role == LiveCallParticipant.ROLE_CANDIDATE and not self.participant.admitted:
            # The evaluator must already be in the room (per product
            # decision - no notifying them outside it) and explicitly
            # admit each candidate the first time they connect. Don't mark
            # connected/broadcast presence/start translation yet - none of
            # that should happen until admitted. This persists on the
            # participant row, so a reconnect after being admitted skips
            # straight to _finish_connect() below instead of re-knocking.
            await self.send_json({"event": "waiting_for_admission"})
            await self.channel_layer.group_send(self.group_name, {
                "type": "join.requested", "sender": self.channel_name,
            })
            return

        await self._finish_connect()

    async def _finish_connect(self):
        await self._set_connected(True)
        await self.channel_layer.group_send(self.group_name, {
            "type": "peer.presence", "sender": self.channel_name, "role": self.role, "connected": True
        })
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
        # Manual turn-by-turn translation is the only supported path for this
        # product: the socket stays open for video signaling and a recorded
        # segment is submitted via the API, transcribed, translated, and then
        # delivered back to the peer. No realtime Azure pipeline is started on
        # connect, and no automatic translation state is broadcast here.

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self._set_connected(False)
            await self.channel_layer.group_send(self.group_name, {
                "type": "peer.presence", "sender": self.channel_name, "role": self.role, "connected": False
            })
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data is not None:
            await self.send_json({"event": "error", "detail": "Manual translation mode is active; audio is sent only when you record a turn."})
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
        elif action == "admit":
            if self.role == LiveCallParticipant.ROLE_EVALUATOR:
                await self._admit_candidate()
                await self.channel_layer.group_send(self.group_name, {
                    "type": "candidate.admitted", "sender": self.channel_name,
                })
        elif action == "deny":
            # No persisted state change - the candidate is left exactly as
            # they were (still waiting, per product decision there's no
            # distinct "denied" state shown to them). This only tells the
            # evaluator's own UI it can drop the prompt for this knock.
            if self.role == LiveCallParticipant.ROLE_EVALUATOR:
                await self.send_json({"event": "join_request_dismissed"})
        else:
            await self.send_json({"event": "error", "detail": "Unsupported action"})

    async def signal_message(self, event):
        if event["sender"] != self.channel_name:
            await self.send_json({**event["payload"], "from": event["sender_role"]})

    async def peer_presence(self, event):
        if event["sender"] != self.channel_name:
            await self.send_json({"event": "peer_presence", "role": event["role"], "connected": event["connected"]})

    async def join_requested(self, event):
        if event["sender"] != self.channel_name and self.role == LiveCallParticipant.ROLE_EVALUATOR:
            await self.send_json({"event": "join_request"})

    async def candidate_admitted(self, event):
        if self.role == LiveCallParticipant.ROLE_CANDIDATE:
            await self.send_json({"event": "admitted"})
            await self._finish_connect()

    async def translation_audio(self, event):
        if event["sender_role"] != self.role:
            await self.send_json({
                "event": "translated_audio", "mime_type": "audio/mpeg",
                "audio": base64.b64encode(event["audio"]).decode("ascii"),
            })

    async def translation_segment(self, event):
        if event.get("recipient_role") == self.role and event.get("sender_role") != self.role:
            await self.send_json({"event": "translation_segment", "payload": event["payload"]})

    async def translation_error(self, event):
        if event["sender_role"] == self.role:
            await self.send_json({"event": "translation_error", "detail": event["detail"]})

    async def call_ended(self, event):
        await self.send_json({"event": "call_ended"})

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
    def _admit_candidate(self):
        LiveCallParticipant.objects.filter(
            call_id=self.participant.call_id, role=LiveCallParticipant.ROLE_CANDIDATE
        ).update(admitted=True)

    @database_sync_to_async
    def _set_connected(self, connected):
        update_participant_presence(self.participant.pk, connected)

    @database_sync_to_async
    def _end_call(self):
        call = LiveCallSession.objects.select_related("interview_session").get(pk=self.participant.call_id)
        LiveCallSession.objects.filter(pk=call.pk).update(
            state=LiveCallSession.STATE_ENDED, ended_at=timezone.now()
        )
        # Ending a live call is also the terminal interview action. Previously
        # this only updated LiveCallSession, leaving the linked InterviewSession
        # and Evaluation stuck IN_PROGRESS indefinitely.
        from api.sessions.services import InterviewSessionService

        session = call.interview_session
        if not session.is_closed():
            InterviewSessionService.complete_session(
                session,
                actor=self.participant.user,
            )
