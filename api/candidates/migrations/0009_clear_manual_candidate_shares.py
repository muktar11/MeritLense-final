"""Sharing candidates with other team members was removed (14 Oct 2026).

A B2B team member now sees assessments only for candidates they added
themselves - that link (candidate.shared_with contains its own creator) is
kept. Every other shared_with entry was granted through the removed Share
feature and is cleared, so nobody keeps access through it.
"""
from django.db import migrations
from django.db.models import F


def clear_manual_shares(apps, schema_editor):
    Candidate = apps.get_model("candidates", "Candidate")
    Candidate.shared_with.through.objects.exclude(user_id=F("candidate__created_by_id")).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("candidates", "0008_registration_lists"),
    ]

    # Not reversible: which shares existed is not recorded anywhere else.
    operations = [
        migrations.RunPython(clear_manual_shares, migrations.RunPython.noop),
    ]
