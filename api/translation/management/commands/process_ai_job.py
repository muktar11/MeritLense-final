from django.core.management.base import BaseCommand, CommandError

from api.core.public_ids import build_object_identifier_filter

class Command(BaseCommand):
    help = "Process a queued AI processing job."

    def add_arguments(self, parser):
        parser.add_argument("--job-id", required=True, help="AIProcessingJob public id or database id")

    def handle(self, *args, **options):
        from api.translation.models import AIProcessingJob
        from api.translation.services import AIProcessingOrchestrationService

        try:
            lookup = build_object_identifier_filter(options["job_id"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        try:
            job = AIProcessingJob.objects.select_related("response", "session", "question").get(**lookup)
        except AIProcessingJob.DoesNotExist as exc:
            raise CommandError("AI processing job not found") from exc

        response = AIProcessingOrchestrationService.run_async_job(job=job, actor=None)
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed job {job.public_id} for response {response.public_id} with status {response.processing_status}"
            )
        )
