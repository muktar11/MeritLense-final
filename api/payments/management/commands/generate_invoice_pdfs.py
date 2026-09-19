from django.core.management.base import BaseCommand, CommandError

from api.payments.invoice_services import generate_invoice_pdf
from api.payments.models import Invoice


class Command(BaseCommand):
    """Generates (or regenerates) the local bilingual PDF for one or more
    already-PAID Invoice rows. Needed as a one-off backfill for invoices
    created before this feature shipped (they have invoice_pdf/
    hosted_invoice_url from Stripe but no local_pdf_file) - going forward,
    StripeService.handle_invoice_paid generates the PDF automatically at
    invoice-creation time, so this command should only be needed for
    backfill or to re-render after a template fix."""

    help = "Generate the local PDF for PAID invoices, by stripe_invoice_id or via --all-missing."

    def add_arguments(self, parser):
        parser.add_argument(
            "stripe_invoice_ids",
            nargs="*",
            help="One or more Invoice.stripe_invoice_id values.",
        )
        parser.add_argument(
            "--all-missing",
            action="store_true",
            help="Process every PAID invoice with no local_pdf_file yet.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Regenerate even if local_pdf_file already exists.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen without writing anything.",
        )

    def handle(self, *args, **options):
        queryset = Invoice.objects.filter(status="PAID")
        if options["all_missing"]:
            targets = queryset.filter(local_pdf_file="")
        else:
            if not options["stripe_invoice_ids"]:
                raise CommandError("Provide one or more stripe_invoice_ids, or pass --all-missing.")
            targets = queryset.filter(stripe_invoice_id__in=options["stripe_invoice_ids"])

        found_ids = set(targets.values_list("stripe_invoice_id", flat=True))
        if not options["all_missing"]:
            for stripe_invoice_id in options["stripe_invoice_ids"]:
                if stripe_invoice_id not in found_ids:
                    self.stderr.write(self.style.ERROR(f"{stripe_invoice_id}: no PAID invoice found."))

        for invoice in targets:
            if invoice.local_pdf_file and not options["force"]:
                self.stdout.write(f"{invoice.stripe_invoice_id}: already has a local PDF - skipping (use --force).")
                continue
            if options["dry_run"]:
                self.stdout.write(f"{invoice.stripe_invoice_id}: would generate.")
                continue

            generate_invoice_pdf(invoice)
            self.stdout.write(
                self.style.SUCCESS(f"{invoice.stripe_invoice_id}: generated - pdf_hash={invoice.pdf_hash[:12]}...")
            )
