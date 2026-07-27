from django.db import migrations


def add_db_guards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    table_name = "reports_evaluationreport"

    if vendor == "sqlite":
        schema_editor.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS reports_evaluationreport_no_update
            BEFORE UPDATE ON {table_name}
            WHEN OLD.report_status != 'ACTIVE' OR NEW.report_status != 'SUPERSEDED'
            BEGIN
                SELECT RAISE(ABORT, 'Evaluation reports are immutable');
            END;
            """
        )
        schema_editor.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS reports_evaluationreport_no_delete
            BEFORE DELETE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'Evaluation reports cannot be deleted');
            END;
            """
        )
        return

    if vendor == "postgresql":
        schema_editor.execute(
            """
            CREATE OR REPLACE FUNCTION reports_prevent_report_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'UPDATE'
                   AND OLD.report_status = 'ACTIVE'
                   AND NEW.report_status = 'SUPERSEDED'
                   AND OLD.report_number = NEW.report_number
                   AND OLD.report_version = NEW.report_version
                   AND OLD.evaluation_id = NEW.evaluation_id
                   AND OLD.session_id = NEW.session_id
                   AND OLD.candidate_id = NEW.candidate_id
                   AND OLD.overall_score = NEW.overall_score
                   AND OLD.max_score = NEW.max_score
                   AND OLD.overall_percentage = NEW.overall_percentage
                   AND OLD.readiness_status = NEW.readiness_status
                   AND OLD.readiness_indicator = NEW.readiness_indicator
                   AND OLD.readiness_reason = NEW.readiness_reason
                   AND OLD.override_triggered = NEW.override_triggered
                   AND OLD.rule_engine_version = NEW.rule_engine_version
                   AND OLD.requires_human_review = NEW.requires_human_review
                   AND OLD.scoring_rule_set_name = NEW.scoring_rule_set_name
                   AND OLD.scoring_rule_version = NEW.scoring_rule_version
                   AND COALESCE(OLD.employer_pdf, '') = COALESCE(NEW.employer_pdf, '')
                   AND COALESCE(OLD.pdf_hash, '') = COALESCE(NEW.pdf_hash, '')
                   AND OLD.report_payload = NEW.report_payload
                   AND OLD.competency_breakdown = NEW.competency_breakdown
                   AND OLD.response_evidence_summary = NEW.response_evidence_summary
                   AND OLD.human_review_flags = NEW.human_review_flags
                   AND OLD.critical_failures = NEW.critical_failures
                   AND COALESCE(OLD.readiness_legal_record_id, 0) = COALESCE(NEW.readiness_legal_record_id, 0)
                   AND COALESCE(OLD.generated_by_id, 0) = COALESCE(NEW.generated_by_id, 0)
                   AND OLD.generated_at = NEW.generated_at
                   AND OLD.metadata = NEW.metadata
                THEN
                    RETURN NEW;
                END IF;

                RAISE EXCEPTION 'Evaluation reports are immutable';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        schema_editor.execute(
            f"""
            DROP TRIGGER IF EXISTS reports_evaluationreport_no_update ON {table_name};
            CREATE TRIGGER reports_evaluationreport_no_update
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION reports_prevent_report_mutation();
            """
        )
        schema_editor.execute(
            f"""
            DROP TRIGGER IF EXISTS reports_evaluationreport_no_delete ON {table_name};
            CREATE TRIGGER reports_evaluationreport_no_delete
            BEFORE DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION reports_prevent_report_mutation();
            """
        )


def remove_db_guards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    table_name = "reports_evaluationreport"

    if vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS reports_evaluationreport_no_update;")
        schema_editor.execute("DROP TRIGGER IF EXISTS reports_evaluationreport_no_delete;")
        return

    if vendor == "postgresql":
        schema_editor.execute(
            f"DROP TRIGGER IF EXISTS reports_evaluationreport_no_update ON {table_name};"
        )
        schema_editor.execute(
            f"DROP TRIGGER IF EXISTS reports_evaluationreport_no_delete ON {table_name};"
        )
        schema_editor.execute("DROP FUNCTION IF EXISTS reports_prevent_report_mutation();")


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0003_alter_evaluationreport_report_status_and_more"),
    ]

    operations = [
        migrations.RunPython(add_db_guards, remove_db_guards),
    ]
