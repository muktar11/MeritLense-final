import re

from django.db import migrations


SKILL_TAG_ALIASES = {
    "safety awareness": "Safety Awareness",
    "safety_awareness": "Safety Awareness",
    "patient safety": "Safety Awareness",
    "hygiene infection control": "Safety Awareness",
    "road load safety": "Safety Awareness",
    "chemical equipment safety": "Safety Awareness",
    "emergency response": "Safety Awareness",
    "agricultural safety": "Safety Awareness",
    "service hygiene knowledge": "Safety Awareness",
    "site safety": "Safety Awareness",
    "knowledge safety": "Safety Awareness",
    "knowledge_score": "Safety Awareness",
    "hygiene_score": "Safety Awareness",
    "behavior integrity": "Behavior & Integrity",
    "behavior_integrity": "Behavior & Integrity",
    "integrity reliability": "Behavior & Integrity",
    "integrity discipline": "Behavior & Integrity",
    "behavior reliability": "Behavior & Integrity",
    "integrity guest relations": "Behavior & Integrity",
    "behavioral_score": "Behavior & Integrity",
    "psych professional": "Psych & Professional",
    "psych_professional": "Psych & Professional",
    "behavior empathy": "Psych & Professional",
    "behavior attention to detail": "Psych & Professional",
    "situational judgment": "Psych & Professional",
    "technical knowledge": "Psych & Professional",
    "task environmental knowledge": "Psych & Professional",
    "task crop knowledge": "Psych & Professional",
    "knowledge task": "Psych & Professional",
    "task_knowledge_score": "Psych & Professional",
    "technical_score": "Psych & Professional",
    "psych_score": "Psych & Professional",
    "task execution": "Task Execution",
    "task_score": "Task Execution",
    "consistency": "Consistency",
    "behavioral consistency": "Consistency",
    "consistency_score": "Consistency",
}


def normalize_key(value):
    return re.sub(r"[^a-z0-9_]+", " ", str(value or "").strip().lower()).strip()


def normalize_skill_tag(value="", scoring_type=""):
    key = normalize_key(value)
    if key in SKILL_TAG_ALIASES:
        return SKILL_TAG_ALIASES[key]
    if str(scoring_type or "").strip().lower() == "completion_pct":
        return "Task Execution"
    return str(value or "").strip()


def forwards(apps, schema_editor):
    SessionQuestion = apps.get_model("interview_sessions", "SessionQuestion")
    for question in SessionQuestion.objects.select_related("question_template").all().iterator():
        template = getattr(question, "question_template", None)
        scoring_type = getattr(template, "scoring_type", "")
        raw_label = (
            (getattr(template, "skill_tag", "") if template is not None else "")
            or question.skill_tag
            or question.skill
        )
        label = normalize_skill_tag(raw_label, scoring_type)
        updates = []
        if question.skill_tag != label:
            question.skill_tag = label
            updates.append("skill_tag")
        if question.skill != label:
            question.skill = label
            updates.append("skill")
        if updates:
            question.save(update_fields=updates)


class Migration(migrations.Migration):

    dependencies = [
        ("questions", "0004_normalize_question_skill_tags"),
        ("interview_sessions", "0012_sessionquestion_skill_tag"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
