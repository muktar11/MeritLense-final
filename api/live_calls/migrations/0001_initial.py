import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("interview_sessions", "0011_interviewsession_scheduling_fields"),
        ("evaluations", "0008_readiness_legal_record_db_guards"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="LiveCallSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("state", models.CharField(choices=[("WAITING", "Waiting"), ("ACTIVE", "Active"), ("RECONNECTING", "Reconnecting"), ("ENDED", "Ended"), ("FAILED", "Failed")], default="WAITING", max_length=20)),
                ("audio_policy", models.CharField(default="REPLACE_ORIGINAL", editable=False, max_length=30)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("evaluation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="live_calls", to="evaluations.evaluation")),
                ("interview_session", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="live_call", to="interview_sessions.interviewsession")),
            ],
        ),
        migrations.CreateModel(
            name="LiveCallParticipant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("role", models.CharField(choices=[("EVALUATOR", "Evaluator"), ("CANDIDATE", "Candidate")], max_length=16)),
                ("input_language", models.CharField(default="en-US", max_length=20)),
                ("output_language", models.CharField(default="en-US", max_length=20)),
                ("connected", models.BooleanField(default=False)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("call", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="participants", to="live_calls.livecallsession")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="live_call_participations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("call", "role"), name="one_participant_per_call_role")]},
        ),
    ]

