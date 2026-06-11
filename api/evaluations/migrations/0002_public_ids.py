import uuid

from django.db import migrations, models


def fill_public_ids(apps, schema_editor):
    evaluation = apps.get_model("evaluations", "Evaluation")
    for obj in evaluation.objects.filter(public_id__isnull=True).only("pk"):
        obj.public_id = uuid.uuid4()
        obj.save(update_fields=["public_id"])


def clear_public_ids(apps, schema_editor):
    apps.get_model("evaluations", "Evaluation").objects.update(public_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ("evaluations", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaluation",
            name="public_id",
            field=models.UUIDField(db_index=True, editable=False, null=True),
        ),
        migrations.RunPython(fill_public_ids, clear_public_ids),
        migrations.AlterField(
            model_name="evaluation",
            name="public_id",
            field=models.UUIDField(
                db_index=True,
                default=uuid.uuid4,
                editable=False,
                unique=True,
            ),
        ),
    ]
