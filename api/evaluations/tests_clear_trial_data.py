"""clear_trial_data / trial_data_inventory: test-only assessment data is
cleared after a backup and confirmation; everything else stays, and the
legal-record protections are back on afterwards."""
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, InternalError, transaction
from django.test import TestCase, override_settings

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.audit.models import AuditLog
from api.candidates.models import Candidate
from api.core.constants import Roles
from api.evaluations.models import Evaluation, EvaluationReadinessDecisionRecord
# Imported as a module: importing the TestCase class by name would make the
# runner collect and run that whole suite again from this module.
from api.evaluations import tests_launch_safeguards as launch
from api.interviews.models import InterviewConfiguration
from api.reports.models import EvaluationReport
from api.reports.services import EvaluationReportService
from api.sessions.models import InterviewSession


@override_settings(**launch.ID_MODE)
class ClearTrialDataTests(TestCase):
    setUpTestData = classmethod(launch.LaunchSafeguardTests.setUpTestData.__func__)
    _assess = launch.LaunchSafeguardTests._assess

    def setUp(self):
        self.seq = 0
        self.tmp = Path(tempfile.mkdtemp())
        # A real business account with its own assessment, on another company.
        self.real_owner = User.objects.create_user(email="real-owner@acme.test", password="x", first_name="R",
                                                   last_name="O", role=Roles.B2B, is_verified=True)
        self.real_company = Company.objects.create(name="Acme", registration_number="AC-1", company_size="11-50",
                                                   phone_number="+1", country="X", city="Y", admin_user=self.real_owner)
        CompanyEmployerProfile.objects.create(user=self.real_owner, company_name="Acme", company=self.real_company,
                                              company_registration_number="AC-1", company_size="11-50")

    def _scored_with_report(self, owner, company):
        self.owner, self.company = owner, company
        evaluation, summary, _ = self._assess("driver")
        EvaluationReportService.generate_for_evaluation(evaluation=evaluation, actor=owner)
        return evaluation

    def _accounts_file(self, emails):
        path = self.tmp / "accounts.json"
        path.write_text(json.dumps(emails), encoding="utf-8")
        return str(path)

    def _backup(self):
        path = self.tmp / "backup.dump"
        path.write_bytes(b"PGDMP")
        return str(path)

    def test_dry_run_changes_nothing_and_execute_clears_only_the_listed_accounts(self):
        test_eval = self._scored_with_report(self.__class__.owner, self.__class__.company)
        real_eval = self._scored_with_report(self.real_owner, self.real_company)
        configs = InterviewConfiguration.objects.count()
        self.assertTrue(EvaluationReadinessDecisionRecord.objects.filter(evaluation=test_eval).exists())
        self.assertTrue(EvaluationReport.objects.filter(evaluation=test_eval).exists())
        accounts = self._accounts_file([test_eval.created_by.email])

        out = StringIO()
        call_command("clear_trial_data", "--accounts", accounts, stdout=out)
        self.assertIn("Dry run", out.getvalue())
        self.assertTrue(Evaluation.objects.filter(pk=test_eval.pk).exists())

        with self.assertRaises(CommandError):  # no backup
            call_command("clear_trial_data", "--accounts", accounts, "--execute", "--confirm-evaluations", "1",
                         stdout=StringIO())
        with self.assertRaises(CommandError):  # count drifted from the dry run
            call_command("clear_trial_data", "--accounts", accounts, "--execute", "--backup-file", self._backup(),
                         "--confirm-evaluations", "7", stdout=StringIO())

        call_command("clear_trial_data", "--accounts", accounts, "--execute", "--backup-file", self._backup(),
                     "--confirm-evaluations", "1", stdout=StringIO())

        self.assertFalse(Evaluation.objects.filter(pk=test_eval.pk).exists())
        self.assertFalse(InterviewSession.objects.filter(pk=test_eval.session_id).exists())
        self.assertFalse(Candidate.objects.filter(pk=test_eval.candidate_id).exists())
        # Everything else stays: the real account's assessment, the test
        # account itself, its company, configuration and the audit trail.
        self.assertTrue(Evaluation.objects.filter(pk=real_eval.pk).exists())
        self.assertTrue(EvaluationReport.objects.filter(evaluation=real_eval).exists())
        self.assertTrue(User.objects.filter(email=test_eval.created_by.email).exists())
        self.assertTrue(Company.objects.filter(pk=self.__class__.company.pk).exists())
        self.assertEqual(InterviewConfiguration.objects.count(), configs)
        self.assertTrue(AuditLog.objects.filter(description__startswith="Trial assessment data cleared").exists())
        # The legal-record protection is back on.
        with self.assertRaises((InternalError, IntegrityError)):
            with transaction.atomic():
                EvaluationReadinessDecisionRecord.objects.filter(evaluation=real_eval).delete()

    def test_all_assessments_clears_every_assessment_and_keeps_accounts_and_configuration(self):
        # Management decision of 22 Oct 2026: every existing assessment is an
        # internal test, whoever created it.
        from api.evaluations.models import Certificate

        first = self._scored_with_report(self.__class__.owner, self.__class__.company)
        second = self._scored_with_report(self.real_owner, self.real_company)
        certificate = Certificate.objects.create(evaluation=first, candidate=first.candidate)
        certificate.pdf_file.save("trial-cert.pdf", ContentFile(b"%PDF-1.4 trial"), save=True)
        stored = certificate.pdf_file.name
        configs, users = InterviewConfiguration.objects.count(), User.objects.count()

        out = StringIO()
        call_command("clear_trial_data", "--all-assessments", stdout=out)
        self.assertIn("every assessment", out.getvalue())
        self.assertIn("stored files", out.getvalue())
        self.assertEqual(Evaluation.objects.count(), 2)
        with self.assertRaises(CommandError):  # not combinable with a candidate list
            call_command("clear_trial_data", "--all-assessments", "--candidates", self._accounts_file(["X"]),
                         stdout=StringIO())

        with self.captureOnCommitCallbacks(execute=True):
            call_command("clear_trial_data", "--all-assessments", "--execute", "--backup-file", self._backup(),
                         "--confirm-evaluations", "2", stdout=StringIO())

        self.assertFalse(Evaluation.objects.exists())
        self.assertFalse(InterviewSession.objects.exists())
        self.assertFalse(Candidate.objects.exists())
        self.assertFalse(EvaluationReport.objects.exists())
        self.assertFalse(EvaluationReadinessDecisionRecord.objects.exists())
        # The certificate and its file are gone: its verification link no
        # longer resolves.
        self.assertFalse(Certificate.objects.exists())
        self.assertFalse(certificate.pdf_file.storage.exists(stored))
        response = self.client.get(f"/api/v1/evaluations/certificates/verify/{certificate.verification_id}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual((User.objects.count(), InterviewConfiguration.objects.count()), (users, configs))
        self.assertTrue(Company.objects.filter(pk=self.real_company.pk).exists())
        # The second assessment was created by another account: cleared too,
        # and the account kept.
        self.assertTrue(User.objects.filter(pk=second.created_by_id).exists())
        entry = AuditLog.objects.get(description__startswith="Trial assessment data cleared: every assessment")
        # The certificate PDF, plus the candidates' documents and report PDFs.
        self.assertEqual(entry.data["scope"], "ALL_ASSESSMENTS")
        self.assertGreaterEqual(entry.data["stored_files_removed"], 1)

    def test_candidate_scope_clears_only_the_listed_qa_candidates_of_the_account(self):
        # QA fixtures loaded into an account that also has a real customer
        # candidate: only the fixtures go, the real candidate stays whole.
        qa_eval = self._scored_with_report(self.__class__.owner, self.__class__.company)
        real_eval = self._scored_with_report(self.__class__.owner, self.__class__.company)
        qa_passport, real_passport = qa_eval.candidate.passport_id, real_eval.candidate.passport_id
        accounts = self._accounts_file([self.__class__.owner.email])
        passports = self.tmp / "passports.json"
        passports.write_text(json.dumps([qa_passport]), encoding="utf-8")

        out = StringIO()
        call_command("clear_trial_data", "--accounts", accounts, "--candidates", str(passports), stdout=out)
        self.assertIn("Evaluations to clear: 1", out.getvalue())
        self.assertIn(qa_passport, out.getvalue())
        self.assertNotIn(real_passport, out.getvalue())

        call_command("clear_trial_data", "--accounts", accounts, "--candidates", str(passports), "--execute",
                     "--backup-file", self._backup(), "--confirm-evaluations", "1", stdout=StringIO())

        self.assertFalse(Candidate.objects.filter(passport_id=qa_passport).exists())
        self.assertFalse(Evaluation.objects.filter(pk=qa_eval.pk).exists())
        self.assertFalse(InterviewSession.objects.filter(pk=qa_eval.session_id).exists())
        self.assertTrue(Candidate.objects.filter(passport_id=real_passport).exists())
        self.assertTrue(Evaluation.objects.filter(pk=real_eval.pk).exists())
        self.assertTrue(InterviewSession.objects.filter(pk=real_eval.session_id).exists())
        self.assertTrue(EvaluationReport.objects.filter(evaluation=real_eval).exists())
        self.assertTrue(AuditLog.objects.filter(
            description__startswith="Trial assessment data cleared for 1 test candidates").exists())

    def test_candidate_scope_refuses_a_passport_id_the_accounts_do_not_own(self):
        foreign = self._scored_with_report(self.real_owner, self.real_company)
        passports = self.tmp / "passports.json"
        passports.write_text(json.dumps([foreign.candidate.passport_id]), encoding="utf-8")
        with self.assertRaises(CommandError):
            call_command("clear_trial_data", "--accounts", self._accounts_file([self.__class__.owner.email]),
                         "--candidates", str(passports), stdout=StringIO())

    def test_refuses_when_an_unlisted_account_assessed_a_test_candidate(self):
        test_eval = self._scored_with_report(self.__class__.owner, self.__class__.company)
        Evaluation.objects.filter(pk=test_eval.pk).update(created_by=self.real_owner)
        with self.assertRaises(CommandError):
            call_command("clear_trial_data", "--accounts", self._accounts_file([self.__class__.owner.email]),
                         stdout=StringIO())

    def test_unknown_account_is_refused(self):
        with self.assertRaises(CommandError):
            call_command("clear_trial_data", "--accounts", self._accounts_file(["nobody@nowhere.test"]),
                         stdout=StringIO())

    def test_inventory_is_read_only_and_suggests_classifications(self):
        self._scored_with_report(self.__class__.owner, self.__class__.company)
        before = Evaluation.objects.count()
        path = self.tmp / "inventory.json"
        call_command("trial_data_inventory", "--json", str(path), stdout=StringIO())
        rows = {r["email"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
        self.assertEqual(Evaluation.objects.count(), before)
        self.assertEqual(rows[self.__class__.owner.email]["evaluations"], 1)
        self.assertEqual(rows[self.__class__.admin.email]["suggested"], "PRESERVE (platform staff)")
