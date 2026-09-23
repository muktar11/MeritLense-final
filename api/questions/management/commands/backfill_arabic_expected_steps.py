from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from api.core.constants import QuestionLifecycleStatus
from api.questions.models import QuestionTemplate
from api.translation.services import TranslationService


class Command(BaseCommand):
    """One-off content backfill: many Arabic-language QuestionTemplate rows
    were created with an empty expected_steps list even though their
    English counterpart (same question_code/role_code/evaluation_tier) has
    one. Since ResponseInterpretationService.build_prompt() only offers the
    AI a closed vocabulary to extract from when the QUESTION'S OWN
    expected_steps is non-empty (see _constrain_to_canonical_steps), an
    Arabic-language interview for one of these questions can never produce
    a matchable indicator - every response scores 0/0 regardless of answer
    quality, independent of AI performance.

    This command translates each such English expected_steps list into
    Arabic (via TranslationService.translate_indicator_phrase, the same
    Google-Translate-backed, cache-first helper already used to render the
    Arabic employer report's Evidence Summary - so most of these phrases
    are already cached and reviewed-in-production translations, not fresh
    machine output) and saves it onto the matching Arabic row.

    Does NOT touch pairs where the English row is ALSO empty - that's a
    separate, much larger rubric-authoring gap (no canonical wording exists
    in any language to translate from), out of scope for a translation
    backfill.
    """

    help = "Backfill empty Arabic QuestionTemplate.expected_steps by translating the English counterpart's."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without saving anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        templates = QuestionTemplate.objects.filter(
            is_active=True, question_status=QuestionLifecycleStatus.ACTIVE
        ).only("id", "question_code", "role_code", "evaluation_tier", "language", "expected_steps")

        by_key = defaultdict(dict)
        for template in templates:
            key = (template.question_code, template.role_code, template.evaluation_tier)
            by_key[key][template.language] = template

        fixed = 0
        skipped_both_empty = 0
        skipped_no_ar_row = 0
        skipped_ar_already_set = 0

        for key, by_language in by_key.items():
            en_template = by_language.get("EN")
            ar_template = by_language.get("AR")
            if en_template is None or ar_template is None:
                if en_template is not None and ar_template is None:
                    skipped_no_ar_row += 1
                continue
            if ar_template.expected_steps:
                skipped_ar_already_set += 1
                continue
            if not en_template.expected_steps:
                skipped_both_empty += 1
                continue

            translated_steps = [
                TranslationService.translate_indicator_phrase(phrase)
                for phrase in en_template.expected_steps
            ]

            self.stdout.write(
                f"{key[1]}/{key[0]} ({key[2]}): {en_template.expected_steps} -> {translated_steps}"
            )
            if not dry_run:
                ar_template.expected_steps = translated_steps
                ar_template.save(update_fields=["expected_steps"])
            fixed += 1

        self.stdout.write(self.style.SUCCESS(
            f"{'Would fix' if dry_run else 'Fixed'}: {fixed}  "
            f"skipped (both empty, nothing to translate): {skipped_both_empty}  "
            f"skipped (no Arabic row exists): {skipped_no_ar_row}  "
            f"skipped (Arabic already set): {skipped_ar_already_set}"
        ))
