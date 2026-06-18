import asyncio
import json
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async

from api.core.public_ids import get_by_identifier
from api.sessions.models import InterviewSession
from api.sessions.realtime import InterviewSessionSocketRegistry
from api.sessions.services import InterviewSessionService, session_event_payload

class InterviewWebSocketApplication:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            await send({"type": "websocket.close", "code": 1003})
            return

        session_id = self._extract_session_id(scope["path"])
        token = self._extract_token(scope.get("query_string", b""))
        if not session_id:
            await send({"type": "websocket.close", "code": 4404})
            return

        session = await sync_to_async(self._get_session)(session_id)
        if session is None or not session.token_is_valid(token):
            await send({"type": "websocket.close", "code": 4403})
            return

        await send({"type": "websocket.accept"})

        connection_id = id(send)
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        await InterviewSessionSocketRegistry.register(str(session.public_id), connection_id, queue, loop)
        sender_task = asyncio.create_task(self._sender(send, queue))

        await send(
            {
                "type": "websocket.send",
                "text": json.dumps(
                    {
                        "event": "SESSION_STATE",
                        **await sync_to_async(session_event_payload)(session),
                    }
                ),
            }
        )

        try:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message["type"] != "websocket.receive":
                    continue

                text = message.get("text") or ""
                if not text:
                    continue
                payload = json.loads(text)
                response = await self._handle_action(session, payload)
                await send({"type": "websocket.send", "text": json.dumps(response)})
        finally:
            sender_task.cancel()
            await InterviewSessionSocketRegistry.unregister(str(session.public_id), connection_id)

    async def _sender(self, send, queue):
        while True:
            message = await queue.get()
            await send({"type": "websocket.send", "text": message})

    async def _handle_action(self, session, payload):
        action = payload.get("action")
        try:
            if action == "ping":
                return {"event": "PONG"}
            if action == "start":
                updated = await sync_to_async(InterviewSessionService.start_session)(session, actor=None)
                return {"event": "SESSION_STARTED", **await sync_to_async(session_event_payload)(updated)}
            if action == "next_question":
                question, completed = await sync_to_async(InterviewSessionService.get_next_question)(session, actor=None)
                if completed or question is None:
                    return {"event": "SESSION_COMPLETED", **await sync_to_async(session_event_payload)(session)}
                return {
                    "event": "QUESTION_ASKED",
                    **await sync_to_async(session_event_payload)(
                        session,
                        {
                            "question_id": str(question.public_id),
                            "order": question.question_order,
                            "text": question.question_text,
                        },
                    ),
                }
            if action == "submit_response":
                question = await sync_to_async(get_by_identifier)(session.questions.all(), payload.get("question_id"))
                response = await sync_to_async(InterviewSessionService.submit_response)(
                    session,
                    question,
                    actor=None,
                    transcript=payload.get("transcript", ""),
                    text_response=payload.get("text_response", ""),
                    duration_seconds=payload.get("duration_seconds", 0),
                )
                return {
                    "event": "ANSWER_RECEIVED",
                    **await sync_to_async(session_event_payload)(
                        session,
                        {
                            "question_id": str(question.public_id),
                            "response_id": str(response.public_id),
                        },
                    ),
                }
            if action == "complete":
                updated = await sync_to_async(InterviewSessionService.complete_session)(session, actor=None)
                return {"event": "SESSION_COMPLETED", **await sync_to_async(session_event_payload)(updated)}
            return {"event": "ERROR", "detail": "Unsupported action"}
        except Exception as exc:
            return {"event": "ERROR", "detail": str(exc)}

    def _extract_session_id(self, path):
        prefix = "/ws/interview/"
        if not path.startswith(prefix):
            return None
        return path[len(prefix):].strip("/").split("/")[0]

    def _extract_token(self, query_string):
        params = parse_qs(query_string.decode())
        return params.get("token", [""])[0]

    def _get_session(self, session_id):
        try:
            return get_by_identifier(InterviewSession.objects.select_related("candidate", "config"), session_id)
        except InterviewSession.DoesNotExist:
            return None
