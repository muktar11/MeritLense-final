from django.core.management.base import BaseCommand, CommandError

from api.evaluations.certificate_services import generate_certificate
from api.evaluations.models import Certificate
from api.core.constants import CertificateStatus


class Command(BaseCommand):
    """Re-runs generate_certificate() for already-issued certificates so
    their stored PDF picks up template/logic fixes made after they were
    first issued (the PDF is a static file - it does not update itself
    when the code that generated it changes). certificate_id and
    assessment_id are untouched; only the PDF content, pdf_hash, and
    issued_at (kept as the original issue date - see generate_certificate)
    are affected. If the evaluation no longer passes certificate_eligibility
    (e.g. consent was revoked since), the certificate is revoked instead of
    silently left alone - that's the correct outcome, but is called out
    explicitly since it's a more consequential change than a PDF refresh.
    """

    help = "Regenerate the stored PDF for one or more already-issued certificates by certificate_id."

    def add_arguments(self, parser):
        parser.add_argument(
            "certificate_ids",
            nargs="+",
            help="One or more certificate_id values, e.g. ML-2026-000005",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen without writing anything.",
        )

    def handle(self, *args, **options):
        certificate_ids = options["certificate_ids"]
        dry_run = options["dry_run"]

        for certificate_id in certificate_ids:
            certificate = (
                Certificate.objects.filter(certificate_id=certificate_id)
                .select_related("evaluation", "evaluation__session")
                .first()
            )
            if certificate is None:
                self.stderr.write(self.style.ERROR(f"{certificate_id}: no certificate found."))
                continue

            evaluation = certificate.evaluation
            if evaluation.certificate_status != CertificateStatus.ISSUED:
                self.stderr.write(
                    self.style.WARNING(
                        f"{certificate_id}: certificate_status is "
                        f"{evaluation.certificate_status!r}, not ISSUED - skipping."
                    )
                )
                continue

            summary = evaluation.session_summaries.select_related("rule_set").first()
            if summary is None:
                self.stderr.write(
                    self.style.ERROR(f"{certificate_id}: no SessionEvaluationSummary found for its evaluation - cannot regenerate.")
                )
                continue

            old_hash = certificate.pdf_hash
            if dry_run:
                self.stdout.write(f"{certificate_id}: would regenerate (dry run, current pdf_hash={old_hash[:12]}...).")
                continue

            regenerated = generate_certificate(evaluation, summary)
            evaluation.refresh_from_db()

            if regenerated is None:
                self.stderr.write(
                    self.style.WARNING(
                        f"{certificate_id}: no longer eligible for a certificate "
                        f"(certificate_status is now {evaluation.certificate_status!r}) - revoked, not regenerated."
                    )
                )
                continue

            if regenerated.pdf_hash == old_hash:
                self.stdout.write(f"{certificate_id}: regenerated, content unchanged (pdf_hash identical).")
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{certificate_id}: regenerated - pdf_hash {old_hash[:12]}... -> {regenerated.pdf_hash[:12]}..."
                    )
                )
