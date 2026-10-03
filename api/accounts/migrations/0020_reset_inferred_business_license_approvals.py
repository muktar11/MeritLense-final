from django.db import migrations


def clear_inferred_approvals(apps, schema_editor):
    Company = apps.get_model("accounts", "Company")
    Company.objects.update(business_license_verified=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_company_business_license_verified"),
    ]

    operations = [
        migrations.RunPython(
            clear_inferred_approvals,
            migrations.RunPython.noop,
        ),
    ]
