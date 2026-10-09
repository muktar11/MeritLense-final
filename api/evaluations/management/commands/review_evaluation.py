from django.core.management.base import BaseCommand, CommandError

from api.accounts.models import User
from api.core.public_ids import get_by_identifier
from api.evaluations.human_review_services import REVIEW_DECISIONS, HumanReviewError, HumanReviewService
from api.evaluations.models import Evaluation


class Command(BaseCommand):
    help = (
        "Human-review release step (documented process). Without --decision, lists results held "
        "from employers. With --evaluation, --decision, --reviewer and --notes, records the "
        "reviewer's decision (audited) and releases the result, report and - only if the normal "
        "eligibility rules pass - certificate."
    )

    def add_arguments(self, parser):
        parser.add_argument("--evaluation", help="Evaluation public ID.")
        parser.add_argument("--decision", choices=REVIEW_DECISIONS)
        parser.add_argument("--reviewer", help="Email of the admin reviewer recording the decision.")
        parser.add_argument("--notes", help="Why the reviewer reached this decision (required).")

    def handle(self, *args, **options):
        if not options["evaluation"]:
            held = Evaluation.objects.filter(review_status=Evaluation.REVIEW_REQUIRED).order_by("completed_at")
            self.stdout.write(f"{held.count()} result(s) awaiting human review:")
            for e in held:
                self.stdout.write(f"  {e.public_id}  automatic={e.readiness_status}  reasons={'; '.join(e.review_reasons)}")
            return
        if not (options["decision"] and options["reviewer"] and options["notes"]):
            raise CommandError("--decision, --reviewer and --notes are all required to release a result.")
        evaluation = get_by_identifier(Evaluation.objects.all(), options["evaluation"])
        reviewer = User.objects.filter(email__iexact=options["reviewer"]).first()
        if reviewer is None:
            raise CommandError(f"No user with email {options['reviewer']}.")
        try:
            evaluation, report = HumanReviewService.approve(
                evaluation=evaluation, reviewer=reviewer, decision=options["decision"], notes=options["notes"])
        except HumanReviewError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Released {evaluation.public_id}: decision {evaluation.review_decision}, "
            f"report {getattr(report, 'report_number', '-')}, certificate {evaluation.certificate_status}."))
