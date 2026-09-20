import uuid

from django.db import migrations, models


def backfill_verification_ids(apps, schema_editor):
    Certificate = apps.get_model('evaluations', 'Certificate')
    for certificate in Certificate.objects.filter(verification_id__isnull=True).iterator():
        Certificate.objects.filter(pk=certificate.pk).update(verification_id=uuid.uuid4())


class Migration(migrations.Migration):

    dependencies = [
        ('evaluations', '0019_evaluatorrating_communication_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='certificate',
            name='verification_id',
            field=models.UUIDField(null=True, unique=True, editable=False, default=None),
        ),
        migrations.RunPython(backfill_verification_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='certificate',
            name='verification_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
