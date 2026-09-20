import json
from pathlib import Path

from django.db import migrations

# Seeded from MeritLense_Master_QA_v4.xlsx (v1.2, 2026-09-20), cross-checked
# against the live faq.json/faq_ar.json for the 35 Public entries (byte-for-
# byte identical question/answer text confirmed before this migration was
# written). The 7 Partner-visibility rows have no Arabic translation yet -
# question_ar/short_answer_ar are intentionally blank for those, not a bug.
_SEED_PATH = Path(__file__).resolve().parent / "knowledge_seed_data.json"


def seed_knowledge_entries(apps, schema_editor):
    KnowledgeEntry = apps.get_model("knowledge", "KnowledgeEntry")
    with open(_SEED_PATH, encoding="utf-8") as f:
        rows = json.load(f)
    for row in rows:
        KnowledgeEntry.objects.create(is_current=True, supersedes=None, **row)


def remove_seeded_entries(apps, schema_editor):
    KnowledgeEntry = apps.get_model("knowledge", "KnowledgeEntry")
    with open(_SEED_PATH, encoding="utf-8") as f:
        rows = json.load(f)
    question_ids = [row["question_id"] for row in rows]
    KnowledgeEntry.objects.filter(question_id__in=question_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_knowledge_entries, remove_seeded_entries),
    ]
