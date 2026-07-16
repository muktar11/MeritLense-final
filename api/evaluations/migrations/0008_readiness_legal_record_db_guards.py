from django.db import migrations


def add_db_guards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    table_name = "evaluations_evaluationreadinessdecisionrecord"

    if vendor == "sqlite":
        schema_editor.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS evaluations_readiness_record_no_update
            BEFORE UPDATE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'Readiness legal records are immutable');
            END;
            """
        )
        schema_editor.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS evaluations_readiness_record_no_delete
            BEFORE DELETE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'Readiness legal records cannot be deleted');
            END;
            """
        )
        return

    if vendor == "postgresql":
        schema_editor.execute(
            """
            CREATE OR REPLACE FUNCTION evaluations_prevent_readiness_record_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Readiness legal records are immutable';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        schema_editor.execute(
            f"""
            DROP TRIGGER IF EXISTS evaluations_readiness_record_no_update ON {table_name};
            CREATE TRIGGER evaluations_readiness_record_no_update
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION evaluations_prevent_readiness_record_mutation();
            """
        )
        schema_editor.execute(
            f"""
            DROP TRIGGER IF EXISTS evaluations_readiness_record_no_delete ON {table_name};
            CREATE TRIGGER evaluations_readiness_record_no_delete
            BEFORE DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION evaluations_prevent_readiness_record_mutation();
            """
        )


def remove_db_guards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    table_name = "evaluations_evaluationreadinessdecisionrecord"

    if vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS evaluations_readiness_record_no_update;")
        schema_editor.execute("DROP TRIGGER IF EXISTS evaluations_readiness_record_no_delete;")
        return

    if vendor == "postgresql":
        schema_editor.execute(
            f"DROP TRIGGER IF EXISTS evaluations_readiness_record_no_update ON {table_name};"
        )
        schema_editor.execute(
            f"DROP TRIGGER IF EXISTS evaluations_readiness_record_no_delete ON {table_name};"
        )
        schema_editor.execute(
            "DROP FUNCTION IF EXISTS evaluations_prevent_readiness_record_mutation();"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("evaluations", "0007_evaluationreadinessdecisionrecord"),
    ]

    operations = [
        migrations.RunPython(add_db_guards, remove_db_guards),
    ]
