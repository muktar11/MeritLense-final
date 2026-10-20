"""Recalculate AI-path results held as "scoring rule awaiting approval".

Run after an approved rubric configuration is deployed (management decision A,
20 Oct 2026; rulebook AUD-02/AUD-03). Each affected session is re-scored
through the normal engine on its pinned rule set, then its certificate and
report follow the usual output path, so release and certificate eligibility
run their normal checks (certificate_eligibility; the earlier report is
superseded, not deleted). Before anything is re-scored, the original summary,
competency and response results and the rule-set / rubric-spec versions that
produced them are preserved in a ScoreRecalculationSnapshot, and the
recalculation is audited. Results with nothing pending are never touched.
"""
import json

from django.core import serializers
from django.core.management.base import BaseCommand
from django.db import transaction

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory
from api.evaluations.models import ScoreRecalculationSnapshot, SessionEvaluationSummary

REASON = "approved rubric configuration"


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
        parser.add_argument("--dry-run", action="store_true", help="List affected assessments only.")

    def handle(self, *args, **options):
        from api.sessions.services import InterviewSessionService

        evaluation_ids = set(
            SessionEvaluationSummary.objects.filter(metadata__rubric_pending_question_codes__0__isnull=False)
            .values_list("evaluation_id", flat=True)
        )
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
                    evaluation=evaluation, reason=REASON, rule_set_versions=rule_sets,
                    rubric_spec_versions=specs, before=capture_scoring_records(evaluation),
                )
            new_summary = InterviewSessionService.generate_completed_session_outputs(evaluation.session)
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
                data={"before": before, "after": after, "reason": REASON,
                      "snapshot_id": str(snapshot.pk), "rubric_spec_versions": {"before": specs, "after": after_specs}},
            )
            recalculated += 1
            still_pending += bool(after["pending"])
        self.stdout.write(self.style.SUCCESS(
            f"{len(seen)} affected; {recalculated} recalculated; {still_pending} still awaiting an approved rule."
        ))
