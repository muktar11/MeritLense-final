import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from api.core.public_ids import get_by_identifier
from api.sessions.models import InterviewSession
from api.sessions.services import InterviewSessionService, session_event_payload


class InterviewSessionConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope["url_route"]["kwargs"]["session_id"]
        self.group_name = f"interview_session_{self.session_id}"
        token = self.scope["query_string"].decode()
        self.token = ""
        if token.startswith("token="):
            self.token = token.split("=", 1)[1]

        self.session = await self._get_session(self.session_id)
        if self.session is None or not await database_sync_to_async(self.session.token_is_valid)(self.token):
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json(
            {
                "event": "SESSION_STATE",
                **await database_sync_to_async(session_event_payload)(self.session),
            }
        )

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        action = content.get("action")
        try:
            if action == "ping":
                await self.send_json({"event": "PONG"})
                return
            if action == "start":
                self.session = await database_sync_to_async(InterviewSessionService.start_session)(self.session, actor=None)
                await self.send_json(
                    {
                        "event": "SESSION_STARTED",
                        **await database_sync_to_async(session_event_payload)(self.session),
                    }
                )
                return
            if action == "next_question":
                question, completed = await database_sync_to_async(InterviewSessionService.get_next_question)(
                    self.session,
                    actor=None,
                )
                self.session = await self._get_session(self.session_id)
                if completed or question is None:
                    await self.send_json(
                        {
                            "event": "SESSION_COMPLETED",
                            **await database_sync_to_async(session_event_payload)(self.session),
                        }
                    )
                    return
                await self.send_json(
                    {
                        "event": "QUESTION_ASKED",
                        **await database_sync_to_async(session_event_payload)(
                            self.session,
                            {
                                "question_id": str(question.public_id),
                                "order": question.question_order,
                                "text": question.question_text,
                            },
                        ),
                    }
                )
                return
            if action == "submit_response":
                question = await database_sync_to_async(get_by_identifier)(self.session.questions.all(), content.get("question_id"))
                response = await database_sync_to_async(InterviewSessionService.submit_response)(
                    self.session,
                    question,
                    actor=None,
                    transcript=content.get("transcript", ""),
                    text_response=content.get("text_response", ""),
                    duration_seconds=content.get("duration_seconds", 0),
                )
                self.session = await self._get_session(self.session_id)
                await self.send_json(
                    {
                        "event": "ANSWER_RECEIVED",
                        **await database_sync_to_async(session_event_payload)(
                            self.session,
                            {
                                "question_id": str(question.public_id),
                                "response_id": str(response.public_id),
                            },
                        ),
                    }
                )
                return
            if action == "complete":
                self.session = await database_sync_to_async(InterviewSessionService.complete_session)(self.session, actor=None)
                await self.send_json(
                    {
                        "event": "SESSION_COMPLETED",
                        **await database_sync_to_async(session_event_payload)(self.session),
                    }
                )
                return
            await self.send_json({"event": "ERROR", "detail": "Unsupported action"})
        except Exception as exc:
            await self.send_json({"event": "ERROR", "detail": str(exc)})

    async def session_event(self, event):
        payload = event["payload"]
        await self.send(text_data=json.dumps(payload))

    @database_sync_to_async
    def _get_session(self, session_id):
        try:
            return get_by_identifier(
                InterviewSession.objects.select_related("candidate", "config").prefetch_related("questions"),
                session_id,
            )
        except InterviewSession.DoesNotExist:
            return None
