import signal
import time

from azure.core.exceptions import ResourceExistsError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from api.storage.services import AzureQueueService


class Command(BaseCommand):
    help = "Continuously consume and process Week 5 AI jobs from Azure Storage Queue."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Exit when the queue is empty")
        parser.add_argument("--max-messages", type=int, default=0, help="Exit after processing this many messages")

    def handle(self, *args, **options):
        queue_service = AzureQueueService()
        if not queue_service.is_configured:
            raise CommandError("AZURE_QUEUE_CONNECTION_STRING and AZURE_DEFAULT_QUEUE_NAME are required")

        self._stopping = False
        for signal_number in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signal_number, self._request_stop)

        queue = queue_service.get_client()
        self._ensure_queue_exists(queue)
        processed_count = 0
        self.stdout.write(f"Listening on Azure queue '{queue_service.queue_name}'")

        while not self._stopping:
            pages = queue.receive_messages(
                messages_per_page=1,
                visibility_timeout=settings.AZURE_QUEUE_VISIBILITY_TIMEOUT_SECONDS,
            ).by_page()
            messages = list(next(pages, []))
            if not messages:
                if options["once"]:
                    break
                time.sleep(settings.AZURE_QUEUE_POLL_INTERVAL_SECONDS)
                continue

            message = messages[0]
            try:
                self._process_message(queue_service, message)
            except Exception as exc:
                self.stderr.write(f"Message {message.id} failed: {exc}")
                if message.dequeue_count >= settings.AZURE_QUEUE_MAX_DEQUEUE_COUNT:
                    self._move_to_poison_queue(queue_service, message, str(exc))
            else:
                queue.delete_message(message.id, message.pop_receipt)
                self.stdout.write(f"Processed Azure queue message {message.id}")

            processed_count += 1
            if options["max_messages"] and processed_count >= options["max_messages"]:
                break

    def _request_stop(self, *_args):
        self._stopping = True

    def _ensure_queue_exists(self, queue):
        try:
            queue.create_queue()
        except ResourceExistsError:
            pass

    def _process_message(self, queue_service, message):
        from api.sessions.models import CandidateResponse
        from api.translation.models import AIProcessingJob
        from api.translation.services import AIProcessingOrchestrationService

        envelope = queue_service.decode_job_message(message.content)
        if envelope.get("job_type") != "PROCESS_AI_RESPONSE":
            raise ValueError(f"Unsupported job type: {envelope.get('job_type')}")

        payload = envelope["payload"]
        response_id = payload.get("response_id")
        idempotency_key = payload.get("idempotency_key")
        if not response_id or not idempotency_key:
            raise ValueError("PROCESS_AI_RESPONSE requires response_id and idempotency_key")

        response = CandidateResponse.objects.get(public_id=response_id)
        job = AIProcessingJob.objects.select_related("response", "session", "question").get(
            response=response,
            idempotency_key=idempotency_key,
        )
        AIProcessingOrchestrationService.run_async_job(job=job, actor=None)

    def _move_to_poison_queue(self, queue_service, message, error):
        poison = queue_service.get_client(settings.AZURE_QUEUE_POISON_NAME)
        self._ensure_queue_exists(poison)
        poison.send_message(message.content)
        queue_service.get_client().delete_message(message.id, message.pop_receipt)
        self.stderr.write(
            f"Moved message {message.id} to '{settings.AZURE_QUEUE_POISON_NAME}' after "
            f"{message.dequeue_count} attempts: {error}"
        )
