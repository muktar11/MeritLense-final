from django.db import migrations, models


def backfill_session_question_skill_tags(apps, schema_editor):
    schema_editor.execute(
        """
        UPDATE interview_sessions_sessionquestion AS sq
        SET skill_tag = COALESCE(NULLIF(qt.skill_tag, ''), NULLIF(qt.skill, ''), NULLIF(sq.skill, ''), '')
        FROM questions_questiontemplate AS qt
        WHERE sq.question_template_id = qt.id
          AND COALESCE(sq.skill_tag, '') <> COALESCE(NULLIF(qt.skill_tag, ''), NULLIF(qt.skill, ''), NULLIF(sq.skill, ''), '')
        """
    )
    schema_editor.execute(
        """
        UPDATE interview_sessions_sessionquestion
        SET skill_tag = COALESCE(NULLIF(skill, ''), '')
        WHERE question_template_id IS NULL
          AND COALESCE(skill_tag, '') = ''
          AND COALESCE(skill, '') <> ''
        """
    )


class Migration(migrations.Migration):

    dependencies = [
        ("interview_sessions", "0011_interviewsession_scheduling_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionquestion",
            name="skill_tag",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.RunPython(backfill_session_question_skill_tags, migrations.RunPython.noop),
    ]
