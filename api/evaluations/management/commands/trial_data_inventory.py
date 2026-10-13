"""Read-only inventory of assessment data per account, to confirm which
records are test-only before clear_trial_data (management decision,
13 Oct 2026). Changes nothing.

Each account gets a suggested classification from obvious markers (test
domains, QA/e2e/fixture addresses, platform staff); everything else is
UNCONFIRMED. The suggestion is only a starting point - management confirms
the final list.

Usage:
    python manage.py trial_data_inventory [--json inventory.json]
"""
import json
import re

from django.core.management.base import BaseCommand
from django.db.models import Max, Q

from api.accounts.models import User
from api.candidates.models import Candidate
from api.core.constants import Roles
from api.evaluations.models import Evaluation
from api.sessions.models import InterviewSession

TEST_MARKERS = re.compile(r"(@example\.(com|org)|@mailinator\.com|e2e|qa-fixture|fixture|live-verify|claude-|test)",
                          re.I)


class Command(BaseCommand):
    help = "Read-only inventory of assessment data per account (for test-only classification)."

    def add_arguments(self, parser):
        parser.add_argument("--json", help="Also write the inventory to this JSON file.")

    def handle(self, *args, **options):
        rows = []
        for user in User.objects.order_by("created_at"):
            candidates = Candidate.objects.filter(created_by=user)
            evaluations = Evaluation.objects.filter(Q(created_by=user) | Q(candidate__created_by=user)).distinct()
            sessions = InterviewSession.objects.filter(Q(created_by=user) | Q(candidate__created_by=user)).distinct()
            if user.role in (Roles.ADMIN, Roles.SUPERADMIN):
                suggestion = "PRESERVE (platform staff)"
            elif TEST_MARKERS.search(user.email) or candidates.filter(email__iregex=TEST_MARKERS.pattern).count() == \
                    candidates.count() > 0:
                suggestion = "TEST"
            else:
                suggestion = "UNCONFIRMED"
            company = getattr(getattr(user, "company_profile", None), "company", None)
            rows.append({
                "email": user.email,
                "role": user.role,
                "company": getattr(company, "name", ""),
                "joined": user.created_at.date().isoformat(),
                "candidates": candidates.count(),
                "evaluations": evaluations.count(),
                "completed_evaluations": evaluations.filter(status="COMPLETED").count(),
                "sessions": sessions.count(),
                "last_activity": (evaluations.aggregate(m=Max("created_at"))["m"] or user.created_at).date().isoformat(),
                "candidate_emails": sorted(candidates.values_list("email", flat=True))[:50],
                "suggested": suggestion,
            })
        for row in rows:
            self.stdout.write(
                f"{row['suggested']:26} {row['email']:48} {row['role']:16} cand {row['candidates']:3} "
                f"eval {row['evaluations']:4} (done {row['completed_evaluations']:3}) sess {row['sessions']:4} "
                f"last {row['last_activity']}")
        if options["json"]:
            with open(options["json"], "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False, indent=1)
            self.stdout.write(f"Wrote {options['json']}")
