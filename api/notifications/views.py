from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Notification

LIST_LIMIT = 50


def _serialize(n):
    return {
        "id": str(n.public_id),
        "title": n.title,
        "body": n.body,
        "action_url": n.action_url or None,
        "created_at": n.created_at,
        "read": n.read_at is not None,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notification_list(request):
    """GET /notifications - the signed-in user's latest notifications and
    how many are unread."""
    mine = Notification.objects.filter(user=request.user)
    return Response({
        "unread_count": mine.filter(read_at__isnull=True).count(),
        "results": [_serialize(n) for n in mine[:LIST_LIMIT]],
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notification_mark_read(request, notification_id):
    """POST /notifications/<id>/read"""
    n = get_object_or_404(Notification, user=request.user, public_id=notification_id)
    if n.read_at is None:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at", "updated_at"])
    return Response(_serialize(n))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notification_mark_all_read(request):
    """POST /notifications/read-all"""
    updated = Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return Response({"updated": updated})
