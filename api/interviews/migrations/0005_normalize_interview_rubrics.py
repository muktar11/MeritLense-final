import re
from collections import defaultdict

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


def dedupe_criteria(items):
    seen = set()
    deduped = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("question_ref") or "").strip(),
            str(item.get("must_include_points") or "").strip(),
            str(item.get("score_note") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def forwards(apps, schema_editor):
    InterviewRubric = apps.get_model("interviews", "InterviewRubric")
    grouped = defaultdict(list)

    for rubric in InterviewRubric.objects.all().order_by("id").iterator():
        label = normalize_skill_tag(rubric.skill_tag or rubric.scoring_category, rubric.scoring_type)
        grouped[(rubric.role_code, rubric.rubric_version, label)].append(rubric)

    for (_, _, label), rows in grouped.items():
        keeper = next(
            (row for row in rows if str(row.skill_tag or "").strip() == label),
            rows[0],
        )
        keeper.skill_tag = label
        keeper.scoring_category = label
        keeper.weight = sum((row.weight for row in rows), 0)
        keeper.max_score = sum((row.max_score for row in rows), 0)
        keeper.scoring_type = next((row.scoring_type for row in rows if row.scoring_type), "")
        keeper.domain = label
        keeper.notes = "\n".join(
            dict.fromkeys(
                str(row.notes or "").strip()
                for row in rows
                if str(row.notes or "").strip()
            )
        )
        keeper.question_set_version = next(
            (row.question_set_version for row in rows if row.question_set_version),
            "",
        )
        keeper.evaluation_criteria = dedupe_criteria(
            [
                item
                for row in rows
                for item in (row.evaluation_criteria or [])
            ]
        )
        keeper.is_active = any(row.is_active for row in rows)
        keeper.save(
            update_fields=[
                "skill_tag",
                "scoring_category",
                "weight",
                "max_score",
                "scoring_type",
                "domain",
                "notes",
                "question_set_version",
                "evaluation_criteria",
                "is_active",
            ]
        )

        for extra in rows[1:]:
            extra.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("interviews", "0004_packagesessionconfig_rolepackagecoverage"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
