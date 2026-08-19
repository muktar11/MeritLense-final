import asyncio
import base64
import json
from unittest.mock import AsyncMock, Mock
from django.test import override_settings
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from api.accounts.models import User
from api.candidates.models import Candidate
from api.core.constants import EvaluationType
from api.evaluations.models import Evaluation
from api.interviews.models import InterviewConfiguration
from api.sessions.models import InterviewSession

from .auth import read_socket_ticket
from .consumers import LiveCallConsumer
from .models import LiveCallParticipant, LiveCallSession
from .services import update_participant_presence


@override_settings(
    WEBRTC_STUN_URLS=["stun:stun.example.test:3478"],
    WEBRTC_TURN_URLS=["turn:turn.example.test:3478"],
    WEBRTC_TURN_USERNAME="user",
    WEBRTC_TURN_CREDENTIAL="secret",
)
class LiveCallApiTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="live-owner@example.test", password="pass", first_name="Live", last_name="Owner"
        )
        candidate = Candidate.objects.create(
            first_name="Candidate", last_name="One", email="candidate@example.test",
            passport_id="LIVE-1", job_role="NA", core_skills="care",
            passport_document="candidate/test.pdf", created_by=self.owner,
        )
        config = InterviewConfiguration.objects.create(role_name="Nanny", role_code="nanny")
        self.session = InterviewSession.objects.create(
            candidate=candidate, config=config, role_name="Nanny", created_by=self.owner,
            scheduled_start_at=timezone.now(), expires_at=timezone.now() + timezone.timedelta(hours=2),
        )

    def test_owner_and_candidate_join_same_linked_call(self):
        self.client.force_authenticate(self.owner)
        owner_response = self.client.post(f"/api/v1/live-calls/sessions/{self.session.public_id}/join")
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(owner_response.data["role"], "EVALUATOR")
        self.assertTrue(owner_response.data["media"]["original_remote_audio_muted"])
        claims = read_socket_ticket(owner_response.data["websocket_ticket"], 60)
        self.assertEqual(claims["role"], "EVALUATOR")

        self.client.force_authenticate(user=None)
        candidate_response = self.client.post(
            f"/api/v1/live-calls/sessions/{self.session.public_id}/join",
            {"token": self.session.access_token}, format="json",
        )
        self.assertEqual(candidate_response.status_code, 200)
        self.assertEqual(candidate_response.data["role"], "CANDIDATE")
        self.assertEqual(LiveCallSession.objects.count(), 1)
        self.assertEqual(LiveCallParticipant.objects.count(), 2)

    def test_join_before_early_join_window_reports_scheduled_start_time(self):
        # A join attempt more than LIVE_CALL_EARLY_JOIN_MINUTES before the
        # scheduled start used to just say "not open yet" with no timing
        # info at all, leaving the candidate/evaluator with no way to know
        # when to come back.
        self.session.scheduled_start_at = timezone.now() + timezone.timedelta(hours=12)
        self.session.save(update_fields=["scheduled_start_at"])

        self.client.force_authenticate(self.owner)
        response = self.client.post(f"/api/v1/live-calls/sessions/{self.session.public_id}/join")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "The live call is not open yet")
        self.assertEqual(response.data["scheduled_start_at"], self.session.scheduled_start_at.isoformat())

    def test_join_response_includes_linked_evaluation_id(self):
        evaluation = Evaluation.objects.create(
            session=self.session,
            candidate=self.session.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(hours=1),
            duration_minutes=45,
            created_by=self.owner,
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(f"/api/v1/live-calls/sessions/{self.session.public_id}/join")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["call"]["evaluation_id"], str(evaluation.public_id))

    def test_join_response_evaluation_id_is_null_without_a_linked_evaluation(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(f"/api/v1/live-calls/sessions/{self.session.public_id}/join")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["call"]["evaluation_id"])

    def test_each_participant_can_set_independent_languages(self):
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/live-calls/sessions/{self.session.public_id}/join")
        response = self.client.put(
            f"/api/v1/live-calls/sessions/{self.session.public_id}/languages",
            {"input_language": "ar-SA", "output_language": "am-ET"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        participant = LiveCallParticipant.objects.get(role="EVALUATOR")
        self.assertEqual(participant.input_language, "ar-SA")
        self.assertEqual(participant.output_language, "am-ET")

    def test_unrelated_user_cannot_join(self):
        stranger = User.objects.create_user(
            email="stranger@example.test", password="pass", first_name="No", last_name="Access"
        )
        self.client.force_authenticate(stranger)
        response = self.client.post(f"/api/v1/live-calls/sessions/{self.session.public_id}/join")
        self.assertEqual(response.status_code, 403)

    @override_settings(WEBRTC_TURN_SECRET="turn-rest-secret")
    def test_join_issues_expiring_turn_rest_credentials(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(f"/api/v1/live-calls/sessions/{self.session.public_id}/join")
        turn = response.data["ice_servers"][1]
        self.assertRegex(turn["username"], r"^\d+:[0-9a-f-]+$")
        self.assertNotEqual(turn["credential"], "secret")


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    AZURE_SPEECH_KEY="test-key",
    AZURE_SPEECH_REGION="test-region",
)
class LiveCallWebSocketTests(TransactionTestCase):
    def setUp(self):
        owner = User.objects.create_user(
            email="socket-owner@example.test", password="pass", first_name="Socket", last_name="Owner"
        )
        candidate = Candidate.objects.create(
            first_name="Socket", last_name="Candidate", email="socket-candidate@example.test",
            passport_id="LIVE-WS-1", job_role="NA", core_skills="care",
            passport_document="candidate/socket.pdf", created_by=owner,
        )
        config = InterviewConfiguration.objects.create(role_name="Nanny", role_code="nanny")
        session = InterviewSession.objects.create(
            candidate=candidate, config=config, role_name="Nanny", created_by=owner,
            scheduled_start_at=timezone.now(), expires_at=timezone.now() + timezone.timedelta(hours=2),
        )
        self.call = LiveCallSession.objects.create(interview_session=session)
        LiveCallParticipant.objects.create(
            call=self.call, role="EVALUATOR", user=owner,
            input_language="ar-SA", output_language="ar-SA",
        )
        LiveCallParticipant.objects.create(
            call=self.call, role="CANDIDATE",
            input_language="am-ET", output_language="en-US",
        )

    def test_signaling_audio_routing_and_reconnect_state(self):
        asyncio.run(self._exercise_protocol())
        evaluator = self.call.participants.get(role="EVALUATOR")
        candidate = self.call.participants.get(role="CANDIDATE")
        update_participant_presence(evaluator.pk, True)
        update_participant_presence(candidate.pk, True)
        self.call.refresh_from_db()
        self.assertEqual(self.call.state, LiveCallSession.STATE_ACTIVE)
        update_participant_presence(evaluator.pk, False)
        self.call.refresh_from_db()
        self.assertEqual(self.call.state, LiveCallSession.STATE_RECONNECTING)

    def test_finish_connect_does_not_start_realtime_translation_pipeline(self):
        async def run_test():
            consumer = LiveCallConsumer()
            consumer.role = "EVALUATOR"
            consumer.channel_name = "test-channel"
            consumer.participant = Mock(pk=123)
            consumer.group_name = f"live_call_{self.call.public_id}"
            consumer.channel_layer = Mock()
            consumer.channel_layer.group_send = AsyncMock()
            consumer.send = AsyncMock()
            consumer._peer_status = AsyncMock(return_value=None)
            consumer._translation_preferences = AsyncMock(return_value=None)
            consumer._set_connected = AsyncMock()

            await consumer._finish_connect()

            consumer.channel_layer.group_send.assert_awaited_once()
            consumer.send.assert_not_called()

        asyncio.run(run_test())

    async def _exercise_protocol(self):
        evaluator = LiveCallConsumer()
        evaluator.role = "EVALUATOR"
        evaluator.channel_name = "evaluator-channel"
        evaluator.group_name = f"live_call_{self.call.public_id}"
        evaluator.channel_layer = Mock()
        evaluator.channel_layer.group_send = AsyncMock()
        evaluator.send = AsyncMock()
        evaluator.pipeline = Mock()

        offer = {"type": "offer", "sdp": "test-sdp"}
        await evaluator.receive(text_data=json.dumps({"action": "offer", "data": offer}))
        evaluator.channel_layer.group_send.assert_awaited_once()
        relayed = evaluator.channel_layer.group_send.await_args.args[1]
        self.assertEqual(relayed["payload"], {"event": "offer", "data": offer})

        await evaluator.receive(bytes_data=b"\x01\x00" * 160)
        error = json.loads(evaluator.send.await_args.kwargs["text_data"])
        self.assertEqual(error["event"], "error")
        self.assertIn("Manual translation mode is active", error["detail"])

        evaluator.role = "CANDIDATE"
        await evaluator.translation_audio({
            "sender_role": "EVALUATOR", "audio": b"mp3-data"
        })
        audio = json.loads(evaluator.send.await_args.kwargs["text_data"])
        self.assertEqual(audio["event"], "translated_audio")
        self.assertEqual(base64.b64decode(audio["audio"]), b"mp3-data")
        self.assertNotIn("text", audio)
