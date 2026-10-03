from django.db import migrations


def migrate_candidate_permission(apps, schema_editor):
    for model_name in ("TeamMemberProfile", "TeamInvitation"):
        model = apps.get_model("accounts", model_name)
        for record in model.objects.all().only("pk", "permissions").iterator():
            permissions = record.permissions or []
            if "view_candidates" not in permissions:
                continue

            migrated = list(dict.fromkeys(
                "add_candidates" if permission == "view_candidates" else permission
                for permission in permissions
            ))
            model.objects.filter(pk=record.pk).update(permissions=migrated)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0015_alter_individualemployerprofile_id_document_and_more"),
    ]

    operations = [
        migrations.RunPython(migrate_candidate_permission, migrations.RunPython.noop),
    ]
