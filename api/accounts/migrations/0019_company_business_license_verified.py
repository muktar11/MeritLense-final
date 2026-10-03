from django.db import migrations, models


def backfill_approved_licenses(apps, schema_editor):
    Company = apps.get_model("accounts", "Company")
    Company.objects.filter(
        is_verified=True,
        employer_profile__resachetified_license__isnull=False,
    ).exclude(
        employer_profile__resachetified_license="",
    ).update(business_license_verified=True)


def clear_business_license_verification(apps, schema_editor):
    Company = apps.get_model("accounts", "Company")
    Company.objects.update(business_license_verified=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0018_alter_companyemployerprofile_resachetified_license"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="business_license_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            backfill_approved_licenses,
            clear_business_license_verification,
        ),
    ]
