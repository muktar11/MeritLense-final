from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("evaluations", "0015_certificate_assessment_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="evaluation",
            name="candidate_job_role",
            field=models.CharField(
                choices=[
                    ("NA", "Nanny"),
                    ("DR", "Driver"),
                    ("HK", "Housekeeper"),
                    ("EC", "Elder Companion"),
                    ("KA", "Kitchen Assistant"),
                    ("MW", "Maintenance Worker"),
                    ("OT", "Other"),
                ],
                max_length=2,
            ),
        ),
    ]
