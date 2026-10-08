from django.core.management.base import BaseCommand
from django.db import transaction

from api.interviews.management.commands.import_governance_question_bank import ARABIC_SEMICOLON, _split_points
from api.questions.models import QuestionTemplate


class Command(BaseCommand):
    help = (
        "Re-split Arabic QuestionTemplate.expected_steps that were imported as a single "
        "step because the Arabic semicolon (؛) was not treated as a delimiter. Only the "
        "split changes - no wording is altered. Affects future interpretations only: "
        "already-interpreted responses, scores and reports are untouched. Dry run unless "
        "--apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the repaired steps (default: dry run).")

    def handle(self, *args, **options):
        candidates = []
        for template in QuestionTemplate.objects.filter(language__iexact="AR").only("id", "question_code", "expected_steps"):
            steps = template.expected_steps or []
            if len(steps) == 1 and ARABIC_SEMICOLON in str(steps[0]):
                repaired = _split_points(steps[0])
                if len(repaired) > 1:
                    candidates.append((template, repaired))

        for template, repaired in candidates[:10]:
            self.stdout.write(f"{template.question_code or template.pk}: 1 step -> {len(repaired)} steps")
        if len(candidates) > 10:
            self.stdout.write(f"... and {len(candidates) - 10} more")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING(f"Dry run: {len(candidates)} Arabic templates would be repaired."))
            return

        with transaction.atomic():
            for template, repaired in candidates:
                QuestionTemplate.objects.filter(pk=template.pk).update(expected_steps=repaired)
        self.stdout.write(self.style.SUCCESS(f"Repaired {len(candidates)} Arabic templates."))
