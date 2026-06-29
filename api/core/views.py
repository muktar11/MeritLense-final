from django.conf import settings
from django.db import connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


def database_status():
    try:
        connection.ensure_connection()
    except Exception:
        return "unavailable"
    return "connected"


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    database = database_status()

    return Response({
        "status": "ok" if database == "connected" else "degraded",
        "service": "AI Interview Backend",
        "version": "1.0.0",
        "database": database,
        "azure_queue": "configured" if settings.AZURE_QUEUE_CONNECTION_STRING else "not_configured",
        "azure_storage": "configured" if settings.AZURE_STORAGE_CONNECTION_STRING else "not_configured",
        "stt_provider": settings.STT_PROVIDER,
        "stt_status": "configured" if settings.STT_API_KEY else "not_configured",
        "tts_provider": settings.TTS_PROVIDER,
        "tts_status": "configured" if settings.GOOGLE_TTS_API_KEY else "not_configured",
    })
