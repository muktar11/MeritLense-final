import base64
import json

from django.conf import settings


class AzureQueueService:
    def __init__(self, queue_name=None):
        self.queue_name = queue_name or settings.AZURE_DEFAULT_QUEUE_NAME
        self.connection_string = settings.AZURE_QUEUE_CONNECTION_STRING

    @property
    def is_configured(self):
        return bool(self.connection_string and self.queue_name)

    def send_job(self, job_type, payload):
        if not self.is_configured:
            return {
                "queued": False,
                "reason": "Azure queue is not configured",
            }

        from azure.core.exceptions import ResourceExistsError
        from azure.storage.queue import QueueClient

        queue = QueueClient.from_connection_string(
            conn_str=self.connection_string,
            queue_name=self.queue_name,
        )
        try:
            queue.create_queue()
        except ResourceExistsError:
            pass

        message = {
            "job_type": job_type,
            "payload": payload,
        }
        encoded = base64.b64encode(json.dumps(message).encode("utf-8")).decode("ascii")
        result = queue.send_message(encoded)

        return {
            "queued": True,
            "queue": self.queue_name,
            "message_id": result.id,
        }


def enqueue_background_job(job_type, payload, queue_name=None):
    return AzureQueueService(queue_name=queue_name).send_job(job_type, payload)
