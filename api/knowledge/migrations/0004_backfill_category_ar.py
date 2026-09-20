from django.db import migrations

# Recovered from the git history of MeritLense-ui/messages/ar/faq.json (the
# same source the original static FAQ page used) - category_id -> Arabic title.
_CATEGORY_AR = {
    "about": "عن MeritLense",
    "how-it-works": "كيف تعمل MeritLense",
    "readiness-framework": "إطار الجاهزية",
    "readiness-results": "نتائج الجاهزية",
    "ai-governance": "الذكاء الاصطناعي والحوكمة",
    "reports-verification": "التقارير والتحقق",
    "differentiation": "التمايز",
    "sectors-roles": "القطاعات والأدوار الوظيفية",
    "business-model": "نموذج العمل",
    "validation-methodology": "التحقق والمنهجية",
}


def backfill_category_ar(apps, schema_editor):
    KnowledgeEntry = apps.get_model("knowledge", "KnowledgeEntry")
    for category_id, title_ar in _CATEGORY_AR.items():
        KnowledgeEntry.objects.filter(category_id=category_id).update(category_ar=title_ar)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0003_knowledgeentry_category_ar"),
    ]

    operations = [
        migrations.RunPython(backfill_category_ar, noop),
    ]
