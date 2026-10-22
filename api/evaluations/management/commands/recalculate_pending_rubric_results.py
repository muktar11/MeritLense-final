"""Re-score explicitly identified assessments held as "scoring rule awaiting
approval", as one named, traceable run.

Run after an approved question rule is deployed (management decision A and
review of 20 Oct 2026; rulebook AUD-02/AUD-03):
- only the evaluations named with --evaluation (public ids) are touched, and
  only if their latest result is still held as pending; nothing else is
  rewritten, and published results with nothing pending never are;
- before re-scoring, the original summary, competency and response results,
  certificate and active report ids, and the rule-set / rubric-spec versions
  that produced them are preserved in a write-once ScoreRecalculationSnapshot
  carrying the run label;
- after re-scoring, the normal approved eligibility rules decide the
  certificate automatically (management direction of 20 Oct 2026); no
  separate release step. --test-data marks the resulting certificates as
  test documents (watermarked, never verified as valid). The earlier report
  is superseded, not deleted; the earlier certificate's record and PDF
  reference are kept in the snapshot.
"""
import json

from django.core import serializers
from django.core.management.base import BaseCommand
from django.db import transaction

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory
from api.evaluations.models import ScoreRecalculationSnapshot, SessionEvaluationSummary

REASON = "approved question scoring rule"


def _rows(queryset):
    return json.loads(serializers.serialize("json", queryset))


def capture_scoring_records(evaluation):
    """Every scoring record of the evaluation exactly as stored now."""
    responses = evaluation.response_results.select_related("rule")
    return {
        "evaluation": _rows(type(evaluation).objects.filter(pk=evaluation.pk)),
        "session_summaries": _rows(evaluation.session_summaries.all()),
        "competency_results": _rows(evaluation.competency_results.all()),
        "response_results": _rows(responses),
        "certificate": _rows(type(evaluation.certificate).objects.filter(pk=evaluation.certificate.pk))
        if hasattr(evaluation, "certificate") else [],
        "active_report_ids": [str(pk) for pk in evaluation.reports.filter(report_status="ACTIVE").values_list("pk", flat=True)],
    }


def _versions(evaluation):
    rule_sets = sorted({
        f"{s.rule_set.version} ({s.rule_set.question_set_version})"
        for s in evaluation.session_summaries.select_related("rule_set")
    })
    specs = sorted({
        r.metadata.get("rubric_spec_version") for r in evaluation.response_results.all()
        if (r.metadata or {}).get("rubric_spec_version")
    })
    return rule_sets, specs


class Command(BaseCommand):
    help = "Re-score assessments that were held because a question's scoring rule awaited approval."

    def add_arguments(self, parser):
        parser.add_argument("--evaluation", action="append", default=[], metavar="PUBLIC_ID",
                            help="Evaluation to re-score (repeatable). Required unless --list-pending.")
        parser.add_argument("--run-label", default="", help="Name of this re-scoring run (required to re-score).")
        parser.add_argument("--list-pending", action="store_true",
                            help="List every evaluation held as pending, without changing anything.")
        parser.add_argument("--dry-run", action="store_true", help="Show what the named evaluations would do.")
        parser.add_argument("--test-data", action="store_true",
                            help="The named evaluations are test assessments: certificates produced for them are "
                                 "marked as test documents, not valid customer certificates.")

    def handle(self, *args, **options):
        from api.sessions.services import InterviewSessionService

        from django.core.management.base import CommandError

        held = SessionEvaluationSummary.objects.filter(metadata__rubric_pending_question_codes__0__isnull=False)
        if options["list_pending"]:
            for summary in held.select_related("evaluation").order_by("evaluation_id", "-generated_at"):
                self.stdout.write(f"{summary.evaluation.public_id}: pending "
                                  f"{', '.join(summary.metadata['rubric_pending_question_codes'])}")
            return
        if not options["evaluation"]:
            raise CommandError("Name the evaluations to re-score with --evaluation (see --list-pending).")
        if not options["dry_run"] and not options["run_label"].strip():
            raise CommandError("Give this re-scoring run a name with --run-label.")
        evaluation_ids = set(
            held.filter(evaluation__public_id__in=options["evaluation"]).values_list("evaluation_id", flat=True)
        )
        found = {str(pk) for pk in held.filter(evaluation__public_id__in=options["evaluation"])
                 .values_list("evaluation__public_id", flat=True)}
        for public_id in options["evaluation"]:
            if public_id not in found:
                self.stdout.write(self.style.WARNING(f"{public_id}: not held as pending - left untouched."))
        seen, recalculated, still_pending = set(), 0, 0
        for evaluation_id in sorted(evaluation_ids):
            summary = (
                SessionEvaluationSummary.objects.filter(evaluation_id=evaluation_id)
                .select_related("evaluation", "evaluation__session").order_by("-generated_at").first()
            )
            if not (summary.metadata or {}).get("rubric_pending_question_codes"):
                continue  # already recalculated
            evaluation = summary.evaluation
            seen.add(evaluation.id)
            before = {
                "pending": summary.metadata.get("rubric_pending_question_codes"),
                "overall_percentage": str(summary.overall_percentage),
                "readiness_status": evaluation.readiness_status,
            }
            if options["dry_run"]:
                self.stdout.write(f"{evaluation.public_id}: pending {', '.join(before['pending'])}")
                continue
            rule_sets, specs = _versions(evaluation)
            with transaction.atomic():
                snapshot = ScoreRecalculationSnapshot.objects.create(
                    evaluation=evaluation, reason=REASON, run_label=options["run_label"].strip(),
                    rule_set_versions=rule_sets,
                    rubric_spec_versions=specs, before=capture_scoring_records(evaluation),
                )
            new_summary = InterviewSessionService.generate_completed_session_outputs(
                evaluation.session, certificate_test_document=options["test_data"])
            evaluation.refresh_from_db()
            after = {
                "pending": (new_summary.metadata if new_summary else {}).get("rubric_pending_question_codes"),
                "overall_percentage": str(new_summary.overall_percentage) if new_summary else None,
                "readiness_status": evaluation.readiness_status,
            }
            _, after_specs = _versions(evaluation)
            snapshot.after = {**after, "rubric_spec_versions": after_specs}
            snapshot.save(update_fields=["after", "updated_at"])
            AuditLogService.log_system(
                action=AuditLogAction.SCORE_SET_RECALCULATED,
                category=AuditLogCategory.EVALUATION,
                description=f"Recalculated evaluation {evaluation.public_id} after scoring-rule approval.",
                resource=evaluation,
                data={"before": before, "after": after, "reason": REASON, "run_label": snapshot.run_label,
                      "test_data": options["test_data"],
                      "snapshot_id": str(snapshot.pk), "rubric_spec_versions": {"before": specs, "after": after_specs}},
            )
            recalculated += 1
            still_pending += bool(after["pending"])
        self.stdout.write(self.style.SUCCESS(
            f"{len(seen)} affected; {recalculated} recalculated; {still_pending} still awaiting an approved rule."
        ))
