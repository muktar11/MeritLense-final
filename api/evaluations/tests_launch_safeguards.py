"""Launch safeguards regression suite (decisions of 9 Oct 2026).

Complete assessments selected by the production code (21-question Full,
7-question Screening) for the approved v1.2 bank, scored through the real
pipeline with indicator-ID extraction on and the whole-bank registry loaded.
Only the LLM is replaced by a controlled extractor, so these prove the rules,
data and release safeguards; live-AI accuracy is covered separately by the
bilingual acceptance run.
"""
import json
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.candidates.models import Candidate
from api.contracts.models import Agreement
from api.core.constants import (
    AgreementMethod, AgreementType, EvaluationType, InterviewEvaluationTier, ReadinessStatus, Roles,
)
from api.evaluations.certificate_services import certificate_eligibility
from api.evaluations.models import Evaluation, EvaluationReadinessCorrection
from api.evaluations.scoring_services import Week6ScoringService
from api.interviews.models import InterviewConfiguration
from api.payments.models import PackageBalance
from api.questions.models import QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession
from api.sessions.services import InterviewSessionService, QuestionGenerationService
from api.translation.services import AIProcessingOrchestrationService, ResponseInterpretationService

VERSION = "GOV1.2-FINAL"
ID_MODE = {"INDICATOR_ID_EXTRACTION_ENABLED": True}


class Extractor:
    """mode: complete | unsafe_with_steps | free_text_risk | low_confidence | none."""

    def __init__(self, mode="complete"):
        self.mode, self.calls = mode, 0

    def interpret(self, *, prompt):
        payload = json.loads(prompt)
        self.calls += 1
        indicators = payload["question"].get("indicators") or []
        mi = [i for i in indicators if i["type"] == "must_include"]
        ni = [i for i in indicators if i["type"] == "negative"]
        obs = [] if self.mode == "none" else [
            {"indicator_id": i["id"], "polarity": "affirmative", "attribution": "self", "quote": "(words)",
             "source_language": "ar", "uncertain": False} for i in mi]
        safety = []
        if self.mode == "unsafe_with_steps" and self.calls == 1 and ni:
            obs.append({"indicator_id": ni[0]["id"], "polarity": "affirmative", "attribution": "self",
                        "quote": "(unsafe words)", "source_language": "en", "uncertain": False})
        if self.mode == "free_text_risk" and self.calls == 1:
            safety = ["candidate says they would skip the safety check to save time"]
        body = {"answer_relevance": "high", "mentioned_steps": [], "missing_steps": [], "safety_risks": safety,
                "compliance_risks": [], "language_quality": "clear",
                "extraction_confidence": 0.5 if (self.mode == "low_confidence" and self.calls == 1) else 0.95,
                "confidence_notes": [], "uncertainty_notes": [], "transcript_issues": [], "key_evidence_phrases": [],
                "observations": obs}
        return {"provider": "STUB", "model": "stub", "raw_content": json.dumps(body, ensure_ascii=False), "metadata": {}}


@override_settings(**ID_MODE)
class LaunchSafeguardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_governance_question_bank", verbosity=0)
        call_command("load_indicator_registry", verbosity=0)
        cls.fixture = json.load(open("api/interviews/fixtures/question_bank/governance_v1_2_corrected.json", encoding="utf-8"))
        cls.owner = User.objects.create_user(email="ls-owner@example.com", password="x", first_name="L", last_name="S",
                                             role=Roles.B2B, is_verified=True)
        cls.company = Company.objects.create(name="Launch Co", registration_number="LS-1", company_size="11-50",
                                             phone_number="+1", country="X", city="Y", admin_user=cls.owner)
        CompanyEmployerProfile.objects.create(user=cls.owner, company_name=cls.company.name, company=cls.company,
                                              company_registration_number="LS-1", company_size="11-50")
        cls.admin = User.objects.create_user(email="ls-admin@example.com", password="x", first_name="Rev", last_name="Iewer",
                                             role=Roles.ADMIN, is_verified=True)

    def setUp(self):
        self.seq = 0

    def _assess(self, role_code, lang="EN", tier=InterviewEvaluationTier.FULL, mode="complete"):
        self.seq += 1
        config = InterviewConfiguration.objects.get(role_code=role_code, language=lang, evaluation_tier=tier,
                                                   question_set_version=VERSION)
        cand = Candidate.objects.create(first_name="C", last_name=str(self.seq), email=f"ls-{self.seq}@example.com",
                                        passport_id=f"LS{self.seq}", job_role="OT", core_skills="", preferred_language=lang,
                                        passport_document="candidates/documents/passport/test.pdf",
                                        created_by=self.owner, company=self.company)
        consent = Agreement.objects.create(user=self.owner, agreement_type=AgreementType.CANDIDATE_CONSENT, version="1",
                                           method=AgreementMethod.CHECKBOX, status="SIGNED", accepted_at=timezone.now())
        full = tier == InterviewEvaluationTier.FULL
        session = InterviewSession.objects.create(
            candidate=cand, organization=self.company, config=config, role_name=config.role_name, role_code=role_code,
            ui_language="EN", candidate_language=lang, tts_language_code="en-US",
            stt_language_code="en-US" if lang == "EN" else "ar-SA", evaluation_tier=tier,
            readiness_indicator_enabled=full, certificate_enabled=full, rubric_version=VERSION,
            question_set_version=VERSION, identity_verified=True, candidate_consent_agreement=consent,
            status="COMPLETED", ended_at=timezone.now(), expires_at=InterviewSession.build_expiry(30), created_by=self.owner)
        questions = QuestionGenerationService.generate_questions(session)
        evaluation = Evaluation.objects.create(
            session=session, candidate=cand, evaluation_type=EvaluationType.INTERVIEW, status="COMPLETED",
            scheduled_date=timezone.now(), duration_minutes=45, created_by=self.owner, company=self.company,
            evaluation_tier=tier, readiness_indicator_enabled=full, certificate_enabled=full)
        extractor = Extractor(mode)
        with mock.patch.object(ResponseInterpretationService, "get_provider", return_value=extractor):
            for q in questions:
                r = CandidateResponse.objects.create(session=session, question=q, transcript="(answer)",
                                                     original_transcript="(answer)", transcript_language=lang.lower())
                AIProcessingOrchestrationService.interpret_response(response=r)
                r.refresh_from_db()
                AIProcessingOrchestrationService.prepare_evaluation_input(response=r)
        summary = Week6ScoringService.run_for_evaluation(evaluation=evaluation)
        evaluation.refresh_from_db()
        return evaluation, summary, len(questions)

    # -- 1. Arabic across the approved bank -----------------------------------

    def test_registry_covers_the_whole_approved_bank(self):
        from api.questions.models import IndicatorDefinition

        self.assertEqual(IndicatorDefinition.objects.values("question_code").distinct().count(), 441)
        self.assertFalse(IndicatorDefinition.objects.exclude(text_ar="").exists())  # no unapproved Arabic (D-01)

    def test_complete_arabic_and_english_full_assessments_are_ready_for_every_role(self):
        for role_code in self.fixture["role_code_map"].values():
            for lang in ("EN", "AR"):
                with self.subTest(role=role_code, lang=lang):
                    evaluation, summary, count = self._assess(role_code, lang)
                    self.assertEqual(count, 21)
                    self.assertEqual(summary.overall_percentage, Decimal("100.00"))
                    self.assertEqual(evaluation.readiness_status, ReadinessStatus.READY)
                    self.assertEqual(evaluation.review_status, Evaluation.REVIEW_NOT_REQUIRED)

    def test_arabic_without_evidence_is_never_ready(self):
        evaluation, summary, _ = self._assess("nursing_assistant", "AR", mode="none")
        self.assertEqual(summary.overall_percentage, Decimal("0.00"))
        self.assertNotEqual(evaluation.readiness_status, ReadinessStatus.READY)

    # -- 2. Screening never issues a certificate --------------------------------

    def test_screening_session_never_enables_certificate_for_any_account_type(self):
        for role in (Roles.B2C, Roles.B2B):
            with self.subTest(account=role):
                user = User.objects.create_user(email=f"scr-{role}@example.com", password="x", first_name="S",
                                                last_name="C", role=role, is_verified=True)
                PackageBalance.objects.create(owner_user=user, balance_type=PackageBalance.SLOTS, fixed_amount=5,
                                              current_balance=5)
                cand = Candidate.objects.create(first_name="S", last_name="C", email=f"scr-c-{role}@example.com",
                                                passport_id=f"SCR-{role}", job_role="OT", core_skills="",
                                                preferred_language="EN",
                                                passport_document="candidates/documents/passport/test.pdf",
                                                created_by=user)
                config = InterviewConfiguration.objects.get(role_code="domestic_worker", language="EN",
                                                           evaluation_tier=InterviewEvaluationTier.SCREENING,
                                                           question_set_version=VERSION)
                try:
                    session = InterviewSessionService.create_session(candidate=cand, config=config, created_by=user)
                except Exception as exc:  # B2B without a company profile may be refused earlier - that is fine
                    self.assertNotIn("certificate", str(exc).lower())
                    continue
                self.assertFalse(session.certificate_enabled)

    def test_certificate_eligibility_refuses_screening_even_if_enabled(self):
        evaluation, summary, count = self._assess("driver", "EN", tier=InterviewEvaluationTier.SCREENING)
        self.assertEqual(count, 7)
        evaluation.certificate_enabled = True  # simulate a mis-configured package
        self.assertEqual(certificate_eligibility(evaluation, summary), (False, "SCREENING_NO_CERTIFICATE"))

    def test_complete_full_assessment_remains_certificate_eligible(self):
        evaluation, summary, _ = self._assess("restaurant_staff", "AR")
        eligible, reason = certificate_eligibility(evaluation, summary)
        self.assertTrue(eligible, reason)

    # -- 3. Unsafe answers cannot produce an unreviewed Ready -----------------

    def test_unsafe_answer_with_all_correct_steps_is_held_not_ready(self):
        for mode in ("unsafe_with_steps", "free_text_risk", "low_confidence"):
            with self.subTest(mode=mode):
                evaluation, summary, _ = self._assess("elderly_caregiver", "AR", mode=mode)
                self.assertEqual(evaluation.review_status, Evaluation.REVIEW_REQUIRED)
                self.assertNotEqual(evaluation.readiness_status, ReadinessStatus.READY)
                self.assertEqual(certificate_eligibility(evaluation, summary), (False, "HUMAN_REVIEW_PENDING"))
                self.assertTrue(evaluation.review_reasons)

    # -- 4/5. Release safeguard ---------------------------------------------

    def test_held_result_is_invisible_to_the_employer_on_every_route(self):
        from api.reports.services import EvaluationReportService

        evaluation, _, _ = self._assess("security_guard", "EN", mode="unsafe_with_steps")
        report = EvaluationReportService.generate_for_evaluation(evaluation=evaluation, actor=self.owner)
        employer = APIClient()
        employer.force_authenticate(self.owner)
        base = f"/api/v1/evaluations/evaluations/{evaluation.public_id}"
        for path in ("report", "scoring-summary", "response-results", "competency-results",
                     "readiness-legal-record", "certificate"):
            with self.subTest(route=path):
                self.assertEqual(employer.get(f"{base}/{path}").status_code, 403)
        detail = employer.get(base).json()
        self.assertIsNone(detail["score"])
        self.assertEqual(detail["readiness_status"], "PENDING")
        self.assertIsNone(detail["latest_report"])
        self.assertEqual(detail["review_status"], "REQUIRED")
        self.assertEqual(employer.get(f"/api/v1/evaluations/reports/{report.public_id}").status_code, 404)
        listed = employer.get("/api/v1/evaluations/reports").json()
        listed = listed.get("results", listed) if isinstance(listed, dict) else listed
        self.assertNotIn(str(report.public_id), [r["id"] for r in listed])
        scores = employer.get("/api/v1/evaluations/candidate-scores").json()
        self.assertNotIn(str(evaluation.public_id), [s["evaluation_id"] for s in scores])
        # The reviewer still sees everything.
        reviewer = APIClient()
        reviewer.force_authenticate(self.admin)
        self.assertEqual(reviewer.get(f"{base}/scoring-summary").status_code, 200)
        queue = reviewer.get("/api/v1/evaluations/evaluations/pending-review").json()
        self.assertIn(str(evaluation.public_id), [q["evaluation_id"] for q in queue])

    def test_only_an_admin_reviewer_can_release_and_release_is_audited(self):
        evaluation, _, _ = self._assess("child_caregiver", "AR", mode="free_text_risk")
        url = f"/api/v1/evaluations/evaluations/{evaluation.public_id}/human-review"
        employer = APIClient()
        employer.force_authenticate(self.owner)
        self.assertEqual(employer.post(url, {"decision": "READY", "notes": "x"}, format="json").status_code, 400)
        reviewer = APIClient()
        reviewer.force_authenticate(self.admin)
        self.assertEqual(reviewer.post(url, {"decision": "READY", "notes": ""}, format="json").status_code, 400)
        response = reviewer.post(url, {"decision": "NOT_READY", "notes": "Answer 1 describes an unsafe act."},
                                 format="json")
        self.assertEqual(response.status_code, 200, response.content)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.review_status, Evaluation.REVIEW_APPROVED)
        self.assertEqual(evaluation.readiness_status, ReadinessStatus.NOT_READY)
        self.assertEqual(evaluation.reviewed_by, self.admin)
        correction = EvaluationReadinessCorrection.objects.get(evaluation=evaluation)
        self.assertEqual(correction.corrected_readiness_status, ReadinessStatus.NOT_READY)
        # Released: the employer can now see the result and its report.
        self.assertEqual(employer.get(f"/api/v1/evaluations/evaluations/{evaluation.public_id}/report").status_code, 200)
        self.assertEqual(evaluation.certificate_status, "NOT_ISSUED")

    def test_reviewer_approval_cannot_override_the_scores_for_a_certificate(self):
        from api.evaluations.human_review_services import HumanReviewService

        evaluation, _, _ = self._assess("nursing_assistant", "EN", mode="none")
        evaluation.review_status = Evaluation.REVIEW_REQUIRED
        evaluation.save(update_fields=["review_status"])
        HumanReviewService.approve(evaluation=evaluation, reviewer=self.admin, decision=ReadinessStatus.READY,
                                   notes="Testing that methodology still applies")
        evaluation.refresh_from_db()
        summary = evaluation.session_summaries.first()
        eligible, reason = certificate_eligibility(evaluation, summary)
        self.assertFalse(eligible)
        self.assertEqual(reason, "REQUIRED_COMPETENCY_BELOW_THRESHOLD")

    def test_initial_launch_setting_holds_every_result_until_reviewed(self):
        with override_settings(REVIEW_ALL_RESULTS=True):
            evaluation, _, _ = self._assess("farm_worker", "AR")
        self.assertEqual(evaluation.readiness_status, ReadinessStatus.READY)
        self.assertEqual(evaluation.review_status, Evaluation.REVIEW_REQUIRED)
        self.assertIn("Initial launch period", " ".join(evaluation.review_reasons))
        evaluation, _, _ = self._assess("farm_worker", "AR")
        self.assertEqual(evaluation.review_status, Evaluation.REVIEW_NOT_REQUIRED)

    # -- 6. Cleanup --------------------------------------------------------------

    def test_cleanup_switches_off_only_test_artifacts(self):
        InterviewConfiguration.objects.create(role_name="Verify", role_code="verify_role", language="EN",
                                              evaluation_tier=InterviewEvaluationTier.FULL, duration_minutes=10,
                                              total_questions=1, allow_retries=True, max_retries=1,
                                              rubric_version="v1", question_set_version="v1", is_active=True)
        for i in range(5):
            QuestionTemplate.objects.create(role_name="Nanny", role_code="NA", language="EN", question_version="",
                                            domain="x", skill="Safety Awareness", question_text=f"Legacy {i}")
        QuestionTemplate.objects.create(role_name="Verify", role_code="verify_role", language="EN",
                                        question_version="1.0", question_code="VERIFY-001", domain="x",
                                        skill="Safety Awareness", question_text="How do you ensure patient safety?")
        approved = QuestionTemplate.objects.filter(question_version=VERSION, is_active=True).count()
        with self.assertRaises(CommandError):
            call_command("deactivate_test_artifacts", "--apply", "--expect-templates", "5", verbosity=0)
        call_command("deactivate_test_artifacts", verbosity=0)  # dry run
        self.assertTrue(InterviewConfiguration.objects.get(role_code="verify_role").is_active)
        call_command("deactivate_test_artifacts", "--apply", verbosity=0)
        self.assertFalse(InterviewConfiguration.objects.get(role_code="verify_role").is_active)
        self.assertFalse(QuestionTemplate.objects.filter(role_code__in=["NA", "verify_role"], is_active=True).exists())
        self.assertEqual(QuestionTemplate.objects.filter(question_version=VERSION, is_active=True).count(), approved)
        self.assertEqual(approved, 882)

    # -- Locks --------------------------------------------------------------------

    def test_session_lengths_unchanged(self):
        for tier, expected in ((InterviewEvaluationTier.SCREENING, 7), (InterviewEvaluationTier.FULL, 21)):
            self.assertEqual(set(InterviewConfiguration.objects.filter(question_set_version=VERSION, evaluation_tier=tier)
                                 .values_list("total_questions", flat=True)), {expected})
