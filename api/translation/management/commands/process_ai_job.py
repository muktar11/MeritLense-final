from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Process one queued Week 5 AI processing job by database job id."

    def add_arguments(self, parser):
        parser.add_argument("--job-id", required=True, help="AIProcessingJob public_id to process")

    def handle(self, *args, **options):
        from api.translation.models import AIProcessingJob
        from api.translation.services import AIProcessingOrchestrationService

        try:
            job = AIProcessingJob.objects.select_related("response", "session", "question").get(
                public_id=options["job_id"]
            )
        except AIProcessingJob.DoesNotExist as exc:
            raise CommandError(f"AI processing job not found: {options['job_id']}") from exc

        result = AIProcessingOrchestrationService.run_async_job(job=job, actor=None)
        self.stdout.write(
            f"Processed job {job.public_id} for response {job.response.public_id} "
            f"with status {result['processing_status']}"
        )
