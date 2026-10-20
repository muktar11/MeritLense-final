"""Activate (cut over to) or roll back a pooled question bank version.

Activation, for every role in the version:
- snapshots the InterviewConfiguration rows it will change (for rollback);
- activates the version's templates, scoring rule sets and rubrics;
- points the role's EN/AR Full and Screening configurations at the version
  (question_set_version, rubric_version, 21 / 7 questions).
The previous version's content stays active: sessions already created on
it keep generating and scoring against their own version.

Rollback restores the snapshot exactly and deactivates the version's
content. Sessions created while it was live keep their own version: their
questions are already generated, and versioned scoring/rubric resolution
does not depend on is_active.

Neither direction modifies any historical session, evaluation, score,
report or certificate.

Requires explicit Production approval before being run in Production.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from api.core.constants import InterviewEvaluationTier, QuestionLifecycleStatus
from api.evaluations.models import ScoringRule, ScoringRuleSet
from api.interviews.models import InterviewConfiguration, InterviewRubric, QuestionBankVersion
from api.questions.models import QuestionTemplate

LANGUAGES = ("EN", "AR")
TIER_COUNTS = {InterviewEvaluationTier.FULL: 21, InterviewEvaluationTier.SCREENING: 7}
SNAPSHOT_FIELDS = ("question_set_version", "rubric_version", "total_questions", "is_active")


def _config_for(role_code, language, tier):
    return (
        InterviewConfiguration.objects.filter(role_code=role_code, language=language, evaluation_tier=tier)
        .order_by("-id")
        .first()
    )


def activate(bank):
    if bank.status == QuestionBankVersion.STATUS_ACTIVE:
        raise CommandError(f"{bank.version} is already active.")
    plan, missing = [], []
    for profile in bank.role_profiles:
        for language in LANGUAGES:
            for tier in TIER_COUNTS:
                config = _config_for(profile["role_code"], language, tier)
                if config is None:
                    missing.append(f"{profile['role_code']}/{language}/{tier}")
                else:
                    plan.append(config)
    if missing:
        raise CommandError("No assessment configuration to point at the new version for: " + ", ".join(missing))

    bank.previous_configuration_state = [
        {"id": config.id, **{field: getattr(config, field) for field in SNAPSHOT_FIELDS}} for config in plan
    ]
    QuestionTemplate.objects.filter(question_set_version=bank.version).update(
        is_active=True, question_status=QuestionLifecycleStatus.ACTIVE,
    )
    rule_sets = ScoringRuleSet.objects.filter(question_set_version=bank.version)
    rule_sets.update(is_active=True)
    # Rule-set ids first: the joined UPDATE got a pathological plan on a
    # freshly loaded table and ran for 15+ minutes in the test suite.
    ScoringRule.objects.filter(rule_set_id__in=list(rule_sets.values_list("id", flat=True))).update(is_active=True)
    InterviewRubric.objects.filter(rubric_version=bank.version).update(is_active=True)
    for config in plan:
        config.question_set_version = bank.version
        config.rubric_version = bank.version
        config.total_questions = TIER_COUNTS[config.evaluation_tier]
        config.is_active = True
        config.save(update_fields=["question_set_version", "rubric_version", "total_questions", "is_active", "updated_at"])
    bank.status = QuestionBankVersion.STATUS_ACTIVE
    bank.activated_at = timezone.now()
    bank.save(update_fields=["status", "activated_at", "previous_configuration_state", "updated_at"])
    return len(plan)


def rollback(bank):
    if bank.status != QuestionBankVersion.STATUS_ACTIVE:
        raise CommandError(f"{bank.version} is not active; nothing to roll back.")
    restored = 0
    for state in bank.previous_configuration_state:
        config = InterviewConfiguration.objects.filter(id=state["id"]).first()
        if config is None:
            continue
        for field in SNAPSHOT_FIELDS:
            setattr(config, field, state[field])
        config.save(update_fields=[*SNAPSHOT_FIELDS, "updated_at"])
        restored += 1
    QuestionTemplate.objects.filter(question_set_version=bank.version).update(
        is_active=False, question_status=QuestionLifecycleStatus.DRAFT,
    )
    # Only the rule sets are switched off: that alone keeps every other
    # session from resolving them. The individual rules stay active so
    # sessions created while this version was live can still be scored.
    ScoringRuleSet.objects.filter(question_set_version=bank.version).update(is_active=False)
    InterviewRubric.objects.filter(rubric_version=bank.version).update(is_active=False)
    bank.status = QuestionBankVersion.STATUS_ROLLED_BACK
    bank.rolled_back_at = timezone.now()
    bank.save(update_fields=["status", "rolled_back_at", "updated_at"])
    return restored


class Command(BaseCommand):
    help = "Activate a pooled question bank version, or roll it back (--rollback). Production use requires approval."

    def add_arguments(self, parser):
        parser.add_argument("version")
        parser.add_argument("--rollback", action="store_true")
        parser.add_argument("--dry-run", action="store_true", help="Run inside a transaction and roll it back.")

    def handle(self, *args, **options):
        bank = QuestionBankVersion.objects.filter(version=options["version"]).first()
        if bank is None:
            raise CommandError(f"Unknown question bank version {options['version']}.")
        with transaction.atomic():
            if options["rollback"]:
                count = rollback(bank)
                message = f"Rolled back {bank.version}: {count} assessment configurations restored."
            else:
                count = activate(bank)
                message = f"Activated {bank.version}: {count} assessment configurations now use it."
            if options["dry_run"]:
                transaction.set_rollback(True)
                message = "Dry run (nothing saved): " + message
        self.stdout.write(self.style.SUCCESS(message))
