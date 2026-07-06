import base64
import json
import os
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


class AzureQueueService:
    def __init__(self, queue_name=None):
        self.queue_name = queue_name or settings.AZURE_DEFAULT_QUEUE_NAME
        self.connection_string = settings.AZURE_QUEUE_CONNECTION_STRING

    @property
    def is_configured(self):
        return bool(self.connection_string and self.queue_name)

    def get_client(self, queue_name=None):
        from azure.storage.queue import QueueClient

        return QueueClient.from_connection_string(
            conn_str=self.connection_string,
            queue_name=queue_name or self.queue_name,
        )

    def send_job(self, job_type, payload):
        if not self.is_configured:
            return {
                "queued": False,
                "reason": "Azure queue is not configured",
            }

        from azure.core.exceptions import ResourceExistsError
        queue = self.get_client()
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

    @staticmethod
    def decode_job_message(content):
        decoded = base64.b64decode(content, validate=True).decode("utf-8")
        message = json.loads(decoded)
        if not isinstance(message, dict) or not isinstance(message.get("payload"), dict):
            raise ValueError("Queue message must contain a job_type and payload object")
        return message


def enqueue_background_job(job_type, payload, queue_name=None):
    return AzureQueueService(queue_name=queue_name).send_job(job_type, payload)


class MediaStorageService:
    @classmethod
    def save_uploaded_file(cls, *, uploaded_file, target_path):
        saved_name = default_storage.save(target_path, uploaded_file)
        return cls._build_file_details(saved_name)

    @classmethod
    def save_bytes(cls, *, content, target_path):
        saved_name = default_storage.save(target_path, ContentFile(content))
        return cls._build_file_details(saved_name)

    @classmethod
    def resolve_url(cls, storage_key):
        if not storage_key:
            return ""
        try:
            return default_storage.url(storage_key)
        except Exception:
            media_url = getattr(settings, "MEDIA_URL", "/media/")
            return os.path.join(media_url.rstrip("/"), storage_key.lstrip("/"))

    @classmethod
    def _build_file_details(cls, storage_key):
        file_size = None
        try:
            file_size = default_storage.size(storage_key)
        except Exception:
            file_size = None

        return {
            "storage_key": storage_key,
            "storage_url": cls.resolve_url(storage_key),
            "file_size_bytes": file_size,
            "filename": Path(storage_key).name,
        }
