from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from api.core.models import TimeStampedModel


class LiveCallSession(TimeStampedModel):
    STATE_WAITING = "WAITING"
    STATE_ACTIVE = "ACTIVE"
    STATE_RECONNECTING = "RECONNECTING"
    STATE_ENDED = "ENDED"
    STATE_FAILED = "FAILED"
    STATE_CHOICES = [(value, value.title()) for value in (
        STATE_WAITING, STATE_ACTIVE, STATE_RECONNECTING, STATE_ENDED, STATE_FAILED
    )]

    AUDIO_REPLACE = "REPLACE_ORIGINAL"

    interview_session = models.OneToOneField(
        "interview_sessions.InterviewSession", on_delete=models.CASCADE, related_name="live_call"
    )
    evaluation = models.ForeignKey(
        "evaluations.Evaluation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="live_calls",
    )
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=STATE_WAITING)
    audio_policy = models.CharField(max_length=30, default=AUDIO_REPLACE, editable=False)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    def clean(self):
        if self.evaluation_id and self.evaluation.session_id != self.interview_session_id:
            raise ValidationError("The evaluation must belong to the same interview session")


class LiveCallParticipant(TimeStampedModel):
    ROLE_EVALUATOR = "EVALUATOR"
    ROLE_CANDIDATE = "CANDIDATE"
    ROLE_CHOICES = [(ROLE_EVALUATOR, "Evaluator"), (ROLE_CANDIDATE, "Candidate")]

    call = models.ForeignKey(LiveCallSession, on_delete=models.CASCADE, related_name="participants")
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="live_call_participations",
    )
    input_language = models.CharField(max_length=20, default="en-US")
    output_language = models.CharField(max_length=20, default="en-US")
    connected = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    # Only meaningful for CANDIDATE - the evaluator is never gated. A
    # candidate connecting for the first time waits in a "knocking" state
    # until the evaluator (who must already be in the room) admits them;
    # this persists so a reconnect after being admitted doesn't re-knock.
    admitted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["call", "role"], name="one_participant_per_call_role")
        ]


class LiveCallSegment(TimeStampedModel):
    """One translated conversation turn, kept so the evaluator can link it
    as evidence on the scorecard (rulebook SCH-02) and the transcript shows
    what was actually said rather than the question bank text."""

    INPUT_AUDIO = "AUDIO"
    INPUT_TEXT = "TEXT"
    INPUT_CHOICES = [(INPUT_AUDIO, "Audio"), (INPUT_TEXT, "Text")]

    call = models.ForeignKey(LiveCallSession, on_delete=models.CASCADE, related_name="segments")
    segment_id = models.UUIDField(unique=True)
    speaker_role = models.CharField(max_length=16, choices=LiveCallParticipant.ROLE_CHOICES)
    input_mode = models.CharField(max_length=10, choices=INPUT_CHOICES, default=INPUT_AUDIO)
    source_language = models.CharField(max_length=20)
    target_language = models.CharField(max_length=20)
    original_text = models.TextField()
    translated_text = models.TextField(blank=True)
    # The competency the evaluator had open in the interview guide. A
    # candidate turn takes the competency of the evaluator turn it answers.
    competency = models.CharField(max_length=40, blank=True)
    sent_at = models.DateTimeField()

    class Meta:
        ordering = ["sent_at", "id"]

