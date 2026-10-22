"""Clear trial assessment data belonging to confirmed test accounts.

Management decision (13 Oct 2026): before real customer assessments,
clear trial assessment data - after a verified backup and confirmation of
which records are test-only - while preserving business accounts,
configuration, access controls and required audit records.

What is cleared, for the accounts listed in --accounts (a JSON list of
emails, confirmed by management):
- candidates those accounts created, and every interview session,
  evaluation, response, score, report, readiness record, certificate and
  slot reservation of those candidates or created by those accounts.

With --candidates (a JSON list of passport IDs), only those candidates of
the listed accounts are cleared, with their sessions, evaluations and
everything under them; the accounts' other candidates and assessments stay.
Use it when QA fixtures were loaded into an account that also holds real
customer data.

With --all-assessments (management decision of 22 Oct 2026: every existing
assessment is an internal test), every candidate, session and evaluation in
the system is cleared, with their reports and certificates, whoever created
them; no account list is needed.

Stored files of the cleared records (certificate and report PDFs, answer
audio, session artifacts, candidate documents) are removed after the
transaction commits, so a cleared certificate's or report's verification
link stops resolving and no copy remains in media storage.

What is never touched: user accounts, companies, subscriptions, package
balances, payments, interview configurations, question banks, rule sets,
permissions, and audit logs (a summary of this run is added to them).

Readiness legal records and reports are protected by database triggers.
Executing disables only their DELETE triggers, inside one transaction that
re-enables them before commit; any error rolls everything back, triggers
included.

Usage:
    python manage.py clear_trial_data --accounts test_accounts.json            # dry run
    python manage.py clear_trial_data --accounts test_accounts.json --execute \\
        --backup-file /path/backup.dump --confirm-evaluations <N from dry run>
    python manage.py clear_trial_data --accounts acct.json --candidates passports.json   # dry run, scoped
    python manage.py clear_trial_data --all-assessments                                   # dry run, everything
"""
import json
from collections import Counter
from pathlib import Path

from django.contrib.admin.utils import NestedObjects
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, router, transaction
from django.db.models import FileField, Q, QuerySet

from api.accounts.models import User
from api.audit.services import AuditLogService
from api.candidates.models import Candidate
from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity
from api.evaluations.models import Evaluation
from api.reports.models import EvaluationReport
from api.sessions.models import InterviewSession

PROTECTED_DELETE_TRIGGERS = (
    ("evaluations_evaluationreadinessdecisionrecord", "evaluations_readiness_record_no_delete"),
    ("reports_evaluationreport", "reports_evaluation_report_guard_delete"),
    ("reports_evaluationreport", "reports_evaluationreport_no_delete"),
)
# Never part of trial data, whatever cascades say.
FORBIDDEN_MODELS = {"accounts.user", "accounts.company", "audit.auditlog", "payments.subscription",
                    "payments.packagebalance", "payments.payment", "interviews.interviewconfiguration"}


class Command(BaseCommand):
    help = "Clear trial assessment data of confirmed test accounts (dry run unless --execute)."

    def add_arguments(self, parser):
        scope = parser.add_mutually_exclusive_group(required=True)
        scope.add_argument("--accounts", help="JSON file: list of test-account emails.")
        scope.add_argument("--all-assessments", action="store_true",
                           help="Every assessment in the system is test data: clear all of them.")
        parser.add_argument("--candidates",
                            help="JSON file: list of candidate passport IDs. Clears only these candidates of the "
                                 "listed accounts and keeps the accounts' other data.")
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--backup-file", help="Verified backup taken immediately before (required to execute).")
        parser.add_argument("--confirm-evaluations", type=int,
                            help="Evaluation count shown by the dry run (required to execute).")

    def handle(self, *args, **options):
        everything = options["all_assessments"]
        if everything and options["candidates"]:
            raise CommandError("--candidates narrows an account list; it cannot be combined with --all-assessments.")
        users = []
        if not everything:
            emails = sorted({e.strip().lower()
                             for e in json.loads(Path(options["accounts"]).read_text(encoding="utf-8"))})
            users = list(User.objects.filter(email__in=emails))
            missing = sorted(set(emails) - {u.email.lower() for u in users})
            if missing:
                raise CommandError(f"Unknown accounts (fix the list): {', '.join(missing)}")

        candidates = Candidate.objects.all() if everything else Candidate.objects.filter(created_by__in=users)
        passport_ids = None
        if everything:
            evaluations = Evaluation.objects.all()
            sessions = InterviewSession.objects.all()
        elif options["candidates"]:
            passport_ids = sorted({str(p).strip() for p in
                                   json.loads(Path(options["candidates"]).read_text(encoding="utf-8"))} - {""})
            if not passport_ids:
                raise CommandError("--candidates lists no passport IDs.")
            candidates = candidates.filter(passport_id__in=passport_ids)
            not_found = sorted(set(passport_ids) - set(candidates.values_list("passport_id", flat=True)))
            if not_found:
                raise CommandError(f"Not candidates of the listed accounts (fix the list): {', '.join(not_found)}")
            # Scoped: only these candidates' assessments, never everything
            # the account created.
            evaluations = Evaluation.objects.filter(candidate__in=candidates).distinct()
            sessions = InterviewSession.objects.filter(candidate__in=candidates).distinct()
        else:
            evaluations = Evaluation.objects.filter(Q(candidate__in=candidates) | Q(created_by__in=users)).distinct()
            sessions = InterviewSession.objects.filter(Q(candidate__in=candidates) | Q(created_by__in=users)).distinct()

        # A test candidate assessed by an account that is NOT on the list
        # means the classification is incomplete - stop, don't guess.
        foreign = evaluations.none() if everything else evaluations.exclude(created_by__in=users)
        if foreign.exists():
            listed = ", ".join(sorted({f"{e.public_id} (by {e.created_by.email})" for e in foreign[:20]}))
            raise CommandError(f"Evaluations by accounts not on the list touch these candidates: {listed}")

        collector = NestedObjects(using=router.db_for_write(Evaluation))
        for group in (evaluations, sessions, candidates):  # one model per collect()
            if group.exists():
                collector.collect(list(group))
        counts = Counter()
        for model, instances in collector.data.items():
            counts[model._meta.label_lower] += len(instances)
        forbidden = sorted(set(counts) & FORBIDDEN_MODELS)
        if forbidden:
            raise CommandError(f"Refusing: the clear would cascade into {', '.join(forbidden)}.")

        reports = EvaluationReport.objects.all() if everything else EvaluationReport.objects.filter(
            Q(evaluation__in=evaluations) | Q(session__in=sessions) | Q(candidate__in=candidates))
        files = sorted({
            (field.storage, getattr(instance, field.name).name)
            for instances in list(collector.data.values()) + [list(reports)]
            for instance in instances
            for field in instance._meta.concrete_fields
            if isinstance(field, FileField) and getattr(instance, field.name)
        }, key=lambda item: item[1])
        if everything:
            self.stdout.write("Scope: every assessment in the system (all accounts).")
        else:
            self.stdout.write(f"Accounts ({len(users)}): {', '.join(u.email for u in users)}")
        if passport_ids is not None:
            self.stdout.write(f"Scoped to candidates ({len(passport_ids)}): "
                              f"{', '.join(f'{c.passport_id} {c.get_full_name()}' for c in candidates)}")
        self.stdout.write(f"Evaluations to clear: {evaluations.count()}  sessions: {sessions.count()}  "
                          f"candidates: {candidates.count()}")
        for label, n in sorted(counts.items()):
            self.stdout.write(f"  {n:6}  {label}")
        self.stdout.write(f"  {reports.count():6}  reports.evaluationreport (cleared first)")
        self.stdout.write(f"  {len(files):6}  stored files (removed after commit)")

        if not options["execute"]:
            self.stdout.write(self.style.WARNING("Dry run - nothing deleted. Re-run with --execute, --backup-file "
                                                 "and --confirm-evaluations to clear."))
            return

        backup = Path(options["backup_file"] or "")
        if not options["backup_file"] or not backup.is_file() or backup.stat().st_size == 0:
            raise CommandError("--backup-file must point to the verified backup taken before this run.")
        if options["confirm_evaluations"] != evaluations.count():
            raise CommandError(f"--confirm-evaluations must equal the dry-run count ({evaluations.count()}).")

        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    # ALTER TABLE is refused while deferred FK checks are
                    # pending; run them now.
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    for table, trigger in PROTECTED_DELETE_TRIGGERS:
                        cursor.execute(f'ALTER TABLE "{table}" DISABLE TRIGGER "{trigger}"')
            # Reports point at readiness records through a PROTECT key, so
            # they go first. Their queryset refuses delete() by design; this
            # sanctioned clear deliberately uses the base implementation.
            report_count = reports.count()
            QuerySet.delete(reports)
            evaluations.delete()
            sessions.delete()
            candidates.delete()
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    for table, trigger in PROTECTED_DELETE_TRIGGERS:
                        cursor.execute(f'ALTER TABLE "{table}" ENABLE TRIGGER "{trigger}"')
            if everything:
                description = "Trial assessment data cleared: every assessment in the system (all internal tests)."
            elif passport_ids is None:
                description = f"Trial assessment data cleared for {len(users)} confirmed test accounts."
            else:
                description = (f"Trial assessment data cleared for {len(passport_ids)} test candidates "
                               f"of {len(users)} accounts.")
            AuditLogService.log_system(
                action=AuditLogAction.EVALUATION_DELETED,
                category=AuditLogCategory.SYSTEM,
                description=description,
                severity=AuditLogSeverity.WARNING,
                data={"scope": "ALL_ASSESSMENTS" if everything else "ACCOUNTS",
                      "accounts": [u.email for u in users], "candidate_passport_ids": passport_ids,
                      "backup_file": backup.name, "counts": {**counts, "reports.evaluationreport": report_count},
                      "stored_files_removed": len(files)},
            )
            transaction.on_commit(lambda: self._remove_files(files))
        self.stdout.write(self.style.SUCCESS(f"Cleared. Backup: {backup.name}. Audit log entry written."))

    def _remove_files(self, files):
        failed = 0
        for storage, name in files:
            try:
                storage.delete(name)
            except Exception:  # a missing or locked file must not undo the committed clear
                failed += 1
        self.stdout.write(f"Stored files removed: {len(files) - failed}; not removable: {failed}.")
