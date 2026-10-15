"""Each company's existing single address becomes its first (MAIN) entry
in the new Business Address(es) list on the Company Profile page."""
import uuid

from django.db import migrations


def seed_main_addresses(apps, schema_editor):
    Company = apps.get_model("accounts", "Company")
    BusinessAddress = apps.get_model("accounts", "BusinessAddress")
    for company in Company.objects.all().iterator():
        if not (company.country or company.city or company.address):
            continue
        if BusinessAddress.objects.filter(company=company, address_type="MAIN").exists():
            continue
        BusinessAddress.objects.create(
            public_id=uuid.uuid4(),
            company=company,
            label="",
            address_type="MAIN",
            country=company.country or "",
            city=company.city or "",
            street=company.address or "",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0025_company_profile_details"),
    ]

    operations = [
        migrations.RunPython(seed_main_addresses, migrations.RunPython.noop),
    ]
