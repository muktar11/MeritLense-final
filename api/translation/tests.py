import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from api.storage.services import AzureQueueService


class AzureQueueWorkerTests(SimpleTestCase):
    def test_decode_job_message_round_trip(self):
        envelope = {
            "job_type": "PROCESS_AI_RESPONSE",
            "payload": {"response_id": "cr_123", "idempotency_key": "request-123"},
        }
        content = base64.b64encode(json.dumps(envelope).encode()).decode()

        self.assertEqual(AzureQueueService.decode_job_message(content), envelope)

    @override_settings(
        AZURE_QUEUE_CONNECTION_STRING="UseDevelopmentStorage=true",
        AZURE_DEFAULT_QUEUE_NAME="meritlense-jobs",
    )
    @patch("api.translation.management.commands.run_ai_queue_worker.AzureQueueService")
    @patch("api.translation.management.commands.run_ai_queue_worker.Command._process_message")
    def test_worker_deletes_successful_message(self, process_message, service_class):
        message = SimpleNamespace(id="message-1", pop_receipt="receipt-1", dequeue_count=1, content="content")
        queue = Mock()
        queue.receive_messages.return_value.by_page.return_value = iter([[message]])
        service_class.return_value.is_configured = True
        service_class.return_value.queue_name = "meritlense-jobs"
        service_class.return_value.get_client.return_value = queue

        call_command("run_ai_queue_worker", max_messages=1)

        process_message.assert_called_once_with(service_class.return_value, message)
        queue.delete_message.assert_called_once_with("message-1", "receipt-1")

    @override_settings(
        AZURE_QUEUE_CONNECTION_STRING="UseDevelopmentStorage=true",
        AZURE_DEFAULT_QUEUE_NAME="meritlense-jobs",
        AZURE_QUEUE_POISON_NAME="meritlense-jobs-poison",
        AZURE_QUEUE_MAX_DEQUEUE_COUNT=5,
    )
    @patch("api.translation.management.commands.run_ai_queue_worker.AzureQueueService")
    @patch("api.translation.management.commands.run_ai_queue_worker.Command._process_message")
    def test_worker_moves_exhausted_message_to_poison_queue(self, process_message, service_class):
        message = SimpleNamespace(id="message-2", pop_receipt="receipt-2", dequeue_count=5, content="content")
        source_queue = Mock()
        poison_queue = Mock()
        source_queue.receive_messages.return_value.by_page.return_value = iter([[message]])
        service = service_class.return_value
        service.is_configured = True
        service.queue_name = "meritlense-jobs"
        service.get_client.side_effect = lambda queue_name=None: poison_queue if queue_name else source_queue
        process_message.side_effect = RuntimeError("provider unavailable")

        call_command("run_ai_queue_worker", max_messages=1)

        poison_queue.create_queue.assert_called_once()
        poison_queue.send_message.assert_called_once_with("content")
        source_queue.delete_message.assert_called_once_with("message-2", "receipt-2")
