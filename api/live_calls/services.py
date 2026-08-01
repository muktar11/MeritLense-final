from django.db import transaction
from django.utils import timezone

from .models import LiveCallParticipant, LiveCallSession


@transaction.atomic
def update_participant_presence(participant_id, connected):
    now = timezone.now()
    participant = LiveCallParticipant.objects.select_for_update().get(pk=participant_id)
    participant.connected = connected
    participant.last_seen_at = now
    participant.save(update_fields=["connected", "last_seen_at", "updated_at"])

    call = LiveCallSession.objects.select_for_update().get(pk=participant.call_id)
    connected_count = call.participants.filter(connected=True).count()
    if connected and connected_count >= 2:
        call.state = LiveCallSession.STATE_ACTIVE
        call.started_at = call.started_at or now
    elif call.state != LiveCallSession.STATE_ENDED:
        call.state = LiveCallSession.STATE_RECONNECTING if call.started_at else LiveCallSession.STATE_WAITING
    call.save(update_fields=["state", "started_at", "updated_at"])
    return call

