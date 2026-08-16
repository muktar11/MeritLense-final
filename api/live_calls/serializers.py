from rest_framework import serializers

from .models import LiveCallParticipant, LiveCallSession


class LiveCallJoinSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True, write_only=True)


class LanguagePreferencesSerializer(serializers.Serializer):
    input_language = serializers.RegexField(r"^[a-z]{2,3}(?:-[A-Z]{2})?$", max_length=20)
    output_language = serializers.RegexField(r"^[a-z]{2,3}(?:-[A-Z]{2})?$", max_length=20)


class LiveCallParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = LiveCallParticipant
        fields = ("role", "input_language", "output_language", "connected", "last_seen_at")


class LiveCallSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    participants = LiveCallParticipantSerializer(many=True, read_only=True)
    evaluation_id = serializers.SerializerMethodField()

    class Meta:
        model = LiveCallSession
        fields = ("id", "state", "audio_policy", "started_at", "ended_at", "participants", "evaluation_id")

    def get_evaluation_id(self, obj):
        return str(obj.evaluation.public_id) if obj.evaluation_id else None
