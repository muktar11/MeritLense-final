import uuid

from django.db import migrations, models


MODELS = ["ScoreCategory", "CandidateScore", "ScoreSet"]


def fill_public_ids(apps, schema_editor):
    for model_name in MODELS:
        model = apps.get_model("scores", model_name)
        for obj in model.objects.filter(public_id__isnull=True).only("pk"):
            obj.public_id = uuid.uuid4()
            obj.save(update_fields=["public_id"])


def clear_public_ids(apps, schema_editor):
    for model_name in MODELS:
        apps.get_model("scores", model_name).objects.update(public_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ("scores", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name=model_name.lower(),
            name="public_id",
            field=models.UUIDField(db_index=True, editable=False, null=True),
        )
        for model_name in MODELS
    ] + [
        migrations.RunPython(fill_public_ids, clear_public_ids),
    ] + [
        migrations.AlterField(
            model_name=model_name.lower(),
            name="public_id",
            field=models.UUIDField(
                db_index=True,
                default=uuid.uuid4,
                editable=False,
                unique=True,
            ),
        )
        for model_name in MODELS
    ]
