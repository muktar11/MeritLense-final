import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AdminAlertConfiguration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("singleton_key", models.CharField(default="default", max_length=32, unique=True)),
                ("settings", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "verbose_name": "Admin Alert Configuration",
                "verbose_name_plural": "Admin Alert Configurations",
            },
        ),
    ]
