from django.core.management.base import BaseCommand, CommandError

from api.accounts.models import User
from api.core.constants import CertificateStatus, Roles
from api.evaluations.certificate_services import generate_certificate
from api.evaluations.models import Evaluation
from api.evaluations.scoring_services import Week6ScoringError, Week6ScoringService
from api.questions.skill_tags import normalize_skill_code
from api.reports.models import EvaluationReport
from api.reports.services import EvaluationReportError, EvaluationReportService
from api.sessions.models import CandidateResponse


class Command(BaseCommand):
    """Re-scores an evaluation against its question bank's CURRENT skill_tag
    values and generates a fresh EvaluationReport, so a taxonomy fix (or
    any other scoring-relevant question-bank change) made after a report
    was first issued actually reaches it. Unlike regenerate_certificate.py,
    there is no in-place refresh: EvaluationReport is hard-immutable (see
    its model docstring), so "regenerating" always means generating a new,
    superseding report version - never edits the old one.

    Backfill step, and why it's needed: EvaluationInputArtifact.competency_code
    is set once, at original AI-interpretation time, from the question
    template's skill_tag AS IT WAS THEN. Week6ScoringService._score_response
    prioritizes that already-persisted value over the ScoringRule's own
    competency_code when re-scoring - so simply re-running scoring does
    NOT pick up a since-changed skill_tag on its own. This command
    refreshes each response's artifact.competency_code from its
    question_template's CURRENT skill_tag first, so a retag actually
    reaches evaluations scored before it happened.

    Also regenerates the certificate (if one is currently issued) for the
    same evaluation afterward, so both documents stay consistent - same
    revoke-if-no-longer-eligible behavior as regenerate_certificate.py.
    """

    help = "Re-score and reissue a report (and certificate, if issued) for one or more evaluations, by EvaluationReport.report_number or Evaluation public_id."

    def add_arguments(self, parser):
        parser.add_argument(
            "identifiers",
            nargs="+",
            help="One or more EvaluationReport.report_number or Evaluation public_id values.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen without writing anything.",
        )

    def handle(self, *args, **options):
        identifiers = options["identifiers"]
        dry_run = options["dry_run"]
        # generate_for_evaluation's audit-log call requires a real user
        # (unlike most actor=None-tolerant services in this codebase), so a
        # management-command run attributes to a super admin account rather
        # than skipping the log entry.
        actor = None
        if not dry_run:
            actor = User.objects.filter(role=Roles.SUPERADMIN, is_active=True).order_by("id").first()
            if actor is None:
                raise CommandError("No active SUPERADMIN user found to attribute this action to.")

        for identifier in identifiers:
            evaluation = self._resolve_evaluation(identifier)
            if evaluation is None:
                self.stderr.write(self.style.ERROR(f"{identifier}: no report or evaluation found."))
                continue

            if dry_run:
                self.stdout.write(f"{identifier}: would re-score and reissue (dry run).")
                continue

            backfilled = self._backfill_artifact_competency_codes(evaluation)

            try:
                Week6ScoringService.run_for_evaluation(evaluation=evaluation)
            except Week6ScoringError as e:
                self.stderr.write(self.style.ERROR(f"{identifier}: re-scoring failed - {e}"))
                continue

            try:
                report = EvaluationReportService.generate_for_evaluation(evaluation=evaluation, actor=actor)
            except EvaluationReportError as e:
                self.stderr.write(self.style.ERROR(f"{identifier}: report generation failed - {e}"))
                continue

            self.stdout.write(
                self.style.SUCCESS(
                    f"{identifier}: reissued as {report.report_number} ({backfilled} response(s) recoded before rescoring)."
                )
            )

            evaluation.refresh_from_db()
            if evaluation.certificate_status == CertificateStatus.ISSUED:
                summary = evaluation.session_summaries.select_related("rule_set").first()
                if summary is None:
                    continue
                regenerated = generate_certificate(evaluation, summary)
                if regenerated is None:
                    self.stderr.write(self.style.WARNING(f"{identifier}: certificate no longer eligible - revoked."))
                else:
                    self.stdout.write(f"{identifier}: certificate {regenerated.certificate_id} also regenerated.")

    def _resolve_evaluation(self, identifier):
        report = EvaluationReport.objects.filter(report_number=identifier).select_related("evaluation").first()
        if report is not None:
            return report.evaluation
        try:
            return Evaluation.objects.get(public_id=identifier)
        except (Evaluation.DoesNotExist, ValueError):
            return None

    def _backfill_artifact_competency_codes(self, evaluation):
        responses = CandidateResponse.objects.filter(session=evaluation.session).select_related(
            "question__question_template", "evaluation_input_artifact"
        )
        count = 0
        for response in responses:
            artifact = getattr(response, "evaluation_input_artifact", None)
            template = getattr(response.question, "question_template", None)
            if artifact is None or template is None:
                continue
            fresh_code = normalize_skill_code(template.skill_id or template.skill_tag or template.skill)
            if fresh_code and fresh_code != artifact.competency_code:
                artifact.competency_code = fresh_code
                artifact.save(update_fields=["competency_code"])
                count += 1
        return count
