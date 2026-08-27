from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("evaluations", "0017_evaluatorrating"),
    ]

    operations = [
        migrations.AlterField(
            model_name="evaluation",
            name="certificate_status",
            field=models.CharField(
                choices=[
                    ("NOT_ISSUED", "Not Issued"),
                    ("PENDING", "Pending"),
                    ("ISSUED", "Issued"),
                    ("REVOKED", "Revoked"),
                    ("EXPIRED", "Expired"),
                ],
                default="NOT_ISSUED",
                help_text="Status of any certificate for this evaluation",
                max_length=20,
            ),
        ),
    ]
