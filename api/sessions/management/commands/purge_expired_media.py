from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity
from api.evaluations.models import Evaluation
from api.sessions.models import CandidateResponse, SessionArtifact

# "Identity verification documents/images" (Annex E) plus the verbal
# confirmation recording, which is itself raw candidate audio, not a
# document - grouped here because both fall under the DPA's 90-day
# raw-media retention window rather than the 24-month evaluation-record
# default.
IDENTITY_ARTIFACT_TYPES = ["ID_DOCUMENT", "SELFIE_IMAGE", "WEBCAM_FRAME", "VERBAL_CONFIRMATION"]

DEFAULT_RETENTION_DAYS = 90


class Command(BaseCommand):
    help = (
        "Deletes raw candidate response audio and identity-verification artifacts "
        "(ID document, selfie, webcam frame, verbal confirmation recording) for "
        "evaluations completed more than the retention window ago (default 90 days). "
        "Transcripts, evidence, and verification status/pass-fail results are never "
        "touched by this command - only the underlying audio/image files are removed, "
        "matching the DPA's Annex E retention schedule; the evaluation record itself "
        "still follows the standard 24-month retention policy."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without deleting anything.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=f"Retention period in days (default: {DEFAULT_RETENTION_DAYS}).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        days = options["days"]
        cutoff = timezone.now() - timedelta(days=days)

        evaluations = Evaluation.objects.filter(
            completed_at__isnull=False,
            completed_at__lte=cutoff,
            session__isnull=False,
        ).select_related("session")

        total_audio = 0
        total_artifacts = 0
        evaluations_touched = 0

        for evaluation in evaluations.iterator():
            session = evaluation.session
            if session is None:
                continue

            responses = list(
                CandidateResponse.objects.filter(session=session).exclude(audio_file="")
            )
            artifacts = list(
                SessionArtifact.objects.filter(
                    session=session, artifact_type__in=IDENTITY_ARTIFACT_TYPES
                ).exclude(file="")
            )

            if not responses and not artifacts:
                continue

            evaluations_touched += 1
            total_audio += len(responses)
            total_artifacts += len(artifacts)

            if dry_run:
                self.stdout.write(
                    f"[dry-run] evaluation {evaluation.id} (completed "
                    f"{evaluation.completed_at.date()}): would delete "
                    f"{len(responses)} response audio file(s), "
                    f"{len(artifacts)} identity-verification artifact(s)"
                )
                continue

            for response in responses:
                response.audio_file.delete(save=False)
                response.audio_url = ""
                response.save(update_fields=["audio_file", "audio_url"])

            for artifact in artifacts:
                artifact.file.delete(save=False)
                artifact.save(update_fields=["file"])

            AuditLogService.log_system(
                action=AuditLogAction.CANDIDATE_UPDATED,
                category=AuditLogCategory.CANDIDATE,
                description=(
                    f"Retention policy: deleted {len(responses)} response audio file(s) "
                    f"and {len(artifacts)} identity-verification artifact(s) for "
                    f"evaluation {evaluation.id} (completed on "
                    f"{evaluation.completed_at.date()}), {days} days past completion. "
                    f"Transcripts, evidence, and verification status were retained."
                ),
                resource=evaluation,
                data={
                    "evaluation_id": evaluation.id,
                    "session_id": str(session.public_id),
                    "audio_files_deleted": len(responses),
                    "artifacts_deleted": len(artifacts),
                    "retention_days": days,
                },
                severity=AuditLogSeverity.INFO,
            )

        verb = "Would delete" if dry_run else "Deleted"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} media for {evaluations_touched} evaluation(s) past the "
                f"{days}-day retention window: {total_audio} response audio file(s), "
                f"{total_artifacts} identity-verification artifact(s)."
            )
        )
