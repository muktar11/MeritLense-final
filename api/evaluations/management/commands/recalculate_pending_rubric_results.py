"""Recalculate AI-path results held as "scoring rule awaiting approval".

Run after an approved rubric configuration is deployed (management decision A,
20 Oct 2026; rulebook AUD-02/AUD-03). Each affected session is re-scored
through the normal engine on its pinned rule set, then its certificate and
report follow the usual output path. Every recalculation is audited with the
before/after result. Results with nothing pending are never touched.
"""
from django.core.management.base import BaseCommand

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory
from api.evaluations.models import SessionEvaluationSummary


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
            new_summary = InterviewSessionService.generate_completed_session_outputs(evaluation.session)
            evaluation.refresh_from_db()
            after = {
                "pending": (new_summary.metadata if new_summary else {}).get("rubric_pending_question_codes"),
                "overall_percentage": str(new_summary.overall_percentage) if new_summary else None,
                "readiness_status": evaluation.readiness_status,
            }
            AuditLogService.log_system(
                action=AuditLogAction.SCORE_SET_RECALCULATED,
                category=AuditLogCategory.EVALUATION,
                description=f"Recalculated evaluation {evaluation.public_id} after scoring-rule approval.",
                resource=evaluation,
                data={"before": before, "after": after, "reason": "approved rubric configuration"},
            )
            recalculated += 1
            still_pending += bool(after["pending"])
        self.stdout.write(self.style.SUCCESS(
            f"{len(seen)} affected; {recalculated} recalculated; {still_pending} still awaiting an approved rule."
        ))
