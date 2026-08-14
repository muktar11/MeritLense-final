import re

from django.db import migrations


SKILL_TAG_CODES = {
    "Safety Awareness": "safety_awareness",
    "Behavior & Integrity": "behavior_integrity",
    "Psych & Professional": "psych_professional",
    "Task Execution": "task_execution",
    "Consistency": "consistency",
}

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
    "patient_safety": "Safety Awareness",
    "hygiene_infection_control": "Safety Awareness",
    "road_load_safety": "Safety Awareness",
    "chemical_equipment_safety": "Safety Awareness",
    "emergency_response": "Safety Awareness",
    "agricultural_safety": "Safety Awareness",
    "service_hygiene_knowledge": "Safety Awareness",
    "site_safety": "Safety Awareness",
    "knowledge_score": "Safety Awareness",
    "hygiene_score": "Safety Awareness",
    "behavior integrity": "Behavior & Integrity",
    "behavior_integrity": "Behavior & Integrity",
    "integrity reliability": "Behavior & Integrity",
    "integrity discipline": "Behavior & Integrity",
    "behavior reliability": "Behavior & Integrity",
    "integrity guest relations": "Behavior & Integrity",
    "integrity_reliability": "Behavior & Integrity",
    "integrity_discipline": "Behavior & Integrity",
    "behavior_reliability": "Behavior & Integrity",
    "integrity_guest_relations": "Behavior & Integrity",
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
    "behavior_empathy": "Psych & Professional",
    "behavior_attention_to_detail": "Psych & Professional",
    "situational_judgment": "Psych & Professional",
    "technical_knowledge": "Psych & Professional",
    "task_environmental_knowledge": "Psych & Professional",
    "task_crop_knowledge": "Psych & Professional",
    "task_knowledge_score": "Psych & Professional",
    "technical_score": "Psych & Professional",
    "psych_score": "Psych & Professional",
    "task execution": "Task Execution",
    "task_execution": "Task Execution",
    "task_score": "Task Execution",
    "consistency": "Consistency",
    "behavioral consistency": "Consistency",
    "consistency_score": "Consistency",
}


def normalize_key(value):
    return re.sub(r"[^a-z0-9_]+", " ", str(value or "").strip().lower()).strip()


def slugify_skill(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def normalize_skill_tag(value="", scoring_type=""):
    key = normalize_key(value)
    if key in SKILL_TAG_ALIASES:
        return SKILL_TAG_ALIASES[key]
    if str(scoring_type or "").strip().lower() == "completion_pct":
        return "Task Execution"
    return str(value or "").strip()


def normalize_skill_code(value="", scoring_type="", fallback=""):
    label = normalize_skill_tag(value=value, scoring_type=scoring_type) or str(fallback or "").strip()
    if label in SKILL_TAG_CODES:
        return SKILL_TAG_CODES[label]
    return slugify_skill(label)


def forwards(apps, schema_editor):
    QuestionTemplate = apps.get_model("questions", "QuestionTemplate")
    for question in QuestionTemplate.objects.all().iterator():
        raw_label = (question.skill_tag or question.skill or "").strip()
        label = normalize_skill_tag(raw_label, question.scoring_type)
        skill = label or (question.skill or question.skill_tag or "").strip()
        code = normalize_skill_code(question.skill_id or raw_label or skill, question.scoring_type, fallback=label)
        updates = []
        if question.skill_tag != label:
            question.skill_tag = label
            updates.append("skill_tag")
        if question.skill != skill:
            question.skill = skill
            updates.append("skill")
        if question.skill_id != code:
            question.skill_id = code
            updates.append("skill_id")
        if updates:
            question.save(update_fields=updates)


class Migration(migrations.Migration):

    dependencies = [
        ("questions", "0003_question_schema_v12"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
