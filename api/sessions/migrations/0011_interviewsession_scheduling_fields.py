from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("interview_sessions", "0010_interviewsession_integrity_violation_count_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="interviewsession",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="interviewsession",
            name="cancellation_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="interviewsession",
            name="scheduled_start_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
