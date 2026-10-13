import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.core.constants import QuestionLifecycleStatus
from api.interviews.models import InterviewConfiguration
from api.questions.models import QuestionTemplate

TEST_ROLE_CODES = ("verify_role", "NA")
APPROVED_VERSION = "GOV1.2-FINAL"
FIXTURE = Path(settings.BASE_DIR) / "api" / "interviews" / "fixtures" / "question_bank" / "governance_v1_2_corrected.json"


class Command(BaseCommand):
    help = (
        "Switch off the obsolete internal test role (verify_role) and the legacy test questions "
        "outside the approved bank. Never touches the approved 441-question bank or any customer "
        "role. Dry run unless --apply; aborts if the matched counts differ from --expect-*."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--expect-configs", type=int, default=1)
        parser.add_argument("--expect-templates", type=int, default=6)

    def handle(self, *args, **options):
        customer_roles = set(json.loads(FIXTURE.read_text(encoding="utf-8"))["role_code_map"].values())
        if customer_roles & set(TEST_ROLE_CODES):
            raise CommandError("A test role code collides with a customer role code; refusing.")

        configs = InterviewConfiguration.objects.filter(role_code__in=TEST_ROLE_CODES, is_active=True)
        templates = QuestionTemplate.objects.filter(role_code__in=TEST_ROLE_CODES, is_active=True).exclude(
            question_version=APPROVED_VERSION)

        for c in configs:
            self.stdout.write(f"config   #{c.pk} {c.role_code} {c.language} {c.evaluation_tier} {c.total_questions}q")
        for t in templates:
            self.stdout.write(f"question #{t.pk} {t.role_code} {t.language} v={t.question_version!r} {t.question_text[:60]}")

        if templates.filter(question_version=APPROVED_VERSION).exists() or templates.filter(
                role_code__in=customer_roles).exists():
            raise CommandError("Matched an approved-bank or customer-role question; refusing.")
        if configs.count() != options["expect_configs"] or templates.count() != options["expect_templates"]:
            raise CommandError(
                f"Expected {options['expect_configs']} config(s) and {options['expect_templates']} question(s), "
                f"found {configs.count()} and {templates.count()}; nothing changed. Re-run with the confirmed counts.")

        approved_before = QuestionTemplate.objects.filter(question_version=APPROVED_VERSION, is_active=True).count()
        if not options["apply"]:
            self.stdout.write(self.style.WARNING(
                f"Dry run: would switch off {configs.count()} config(s) and {templates.count()} question(s). "
                f"Approved-bank active questions untouched: {approved_before}."))
            return

        with transaction.atomic():
            configs.update(is_active=False)
            templates.update(is_active=False, question_status=QuestionLifecycleStatus.ARCHIVED)
            approved_after = QuestionTemplate.objects.filter(question_version=APPROVED_VERSION, is_active=True).count()
            if approved_after != approved_before:
                raise CommandError("Approved-bank question count changed; rolled back.")
        self.stdout.write(self.style.SUCCESS(
            f"Switched off {options['expect_configs']} config(s) and {options['expect_templates']} question(s). "
            f"Approved-bank active questions: {approved_after} (unchanged)."))
