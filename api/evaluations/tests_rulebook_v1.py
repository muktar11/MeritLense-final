"""Rulebook v1.0 corrections (docs/ASSESSMENT_RULES.md).

Covers the fully defined corrections on feat/rulebook-v1-corrections:
CAL-03 assigned-question reconciliation, FIX-02 indicator weights, CER-02
scheduled Not Ready certificates, SCH-07 no AI overwrite of evaluator
outcomes, OUT-06 labels, DOC-03/04/05 report content. The LLM is replaced by
the controlled extractor used in the launch safeguards suite.
"""
import json
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.candidates.models import Candidate
from api.contracts.models import Agreement
from api.core.constants import AgreementMethod, AgreementType, EvaluationType, InterviewEvaluationTier, Roles
from api.evaluations.certificate_services import certificate_eligibility
from api.evaluations.models import Evaluation, EvaluatorRating, ScoringRule, SessionEvaluationSummary
from api.evaluations.readiness_record_services import EvaluationReadinessRecordService
from api.evaluations.scoring_services import Week6ScoringService
from api.evaluations.tests_launch_safeguards import Extractor
from api.interviews.management.commands.import_governance_question_bank import _weighted_indicators
from api.interviews.models import InterviewConfiguration
from api.reports.services import EvaluationReportService
from api.sessions.models import CandidateResponse, InterviewSession
from api.sessions.services import QuestionGenerationService
from api.translation.services import AIProcessingOrchestrationService, ResponseInterpretationService

VERSION = "GOV1.2-FINAL"


@override_settings(INDICATOR_ID_EXTRACTION_ENABLED=True)
class RulebookScoringTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_governance_question_bank", verbosity=0)
        call_command("load_indicator_registry", verbosity=0)
        cls.owner = User.objects.create_user(email="rb-owner@example.com", password="x", first_name="R", last_name="B",
                                             role=Roles.B2B, is_verified=True)
        cls.company = Company.objects.create(name="Rulebook Co", registration_number="RB-1", company_size="11-50",
                                             phone_number="+1", country="X", city="Y", admin_user=cls.owner)
        CompanyEmployerProfile.objects.create(user=cls.owner, company_name=cls.company.name, company=cls.company,
                                              company_registration_number="RB-1", company_size="11-50")

    def setUp(self):
        self.seq = 0

    def _assess(self, role_code="driver", skip_orders=(), scheduled=False, before_scoring=None):
        self.seq += 1
        tier = InterviewEvaluationTier.FULL
        config = InterviewConfiguration.objects.get(role_code=role_code, language="EN", evaluation_tier=tier,
                                                   question_set_version=VERSION)
        cand = Candidate.objects.create(first_name="C", last_name=str(self.seq), email=f"rb-{self.seq}@example.com",
                                        passport_id=f"RB{self.seq}", job_role="OT", core_skills="", preferred_language="EN",
                                        passport_document="candidates/documents/passport/test.pdf",
                                        created_by=self.owner, company=self.company)
        consent = Agreement.objects.create(user=self.owner, agreement_type=AgreementType.CANDIDATE_CONSENT, version="1",
                                           method=AgreementMethod.CHECKBOX, status="SIGNED", accepted_at=timezone.now())
        session = InterviewSession.objects.create(
            candidate=cand, organization=self.company, config=config, role_name=config.role_name, role_code=role_code,
            ui_language="EN", candidate_language="EN", tts_language_code="en-US", stt_language_code="en-US",
            evaluation_tier=tier, readiness_indicator_enabled=True, certificate_enabled=True, rubric_version=VERSION,
            question_set_version=VERSION, identity_verified=True, candidate_consent_agreement=consent,
            scheduled_start_at=timezone.now() - timezone.timedelta(hours=1) if scheduled else None,
            status="COMPLETED", ended_at=timezone.now(), expires_at=InterviewSession.build_expiry(30), created_by=self.owner)
        questions = QuestionGenerationService.generate_questions(session)
        evaluation = Evaluation.objects.create(
            session=session, candidate=cand, evaluation_type=EvaluationType.INTERVIEW, status="COMPLETED",
            scheduled_date=timezone.now(), duration_minutes=45, created_by=self.owner, company=self.company,
            evaluation_tier=tier, readiness_indicator_enabled=True, certificate_enabled=True)
        with mock.patch.object(ResponseInterpretationService, "get_provider", return_value=Extractor("complete")):
            for q in questions:
                if q.question_order in skip_orders:
                    continue
                r = CandidateResponse.objects.create(session=session, question=q, transcript="(answer)",
                                                     original_transcript="(answer)", transcript_language="en")
                AIProcessingOrchestrationService.interpret_response(response=r)
                r.refresh_from_db()
                AIProcessingOrchestrationService.prepare_evaluation_input(response=r)
        if before_scoring:
            before_scoring(evaluation)
        summary = Week6ScoringService.run_for_evaluation(evaluation=evaluation)
        evaluation.refresh_from_db()
        return evaluation, summary

    def test_complete_assessment_reconciles_every_assigned_question(self):
        evaluation, summary = self._assess()
        self.assertEqual(summary.metadata["assigned_question_count"], 21)
        self.assertEqual(summary.metadata["unanswered_question_count"], 0)
        self.assertNotEqual(summary.status, SessionEvaluationSummary.STATUS_PARTIALLY_EVALUATED)

    def test_unanswered_question_is_non_completion_not_a_silent_drop(self):
        # CAL-03: a question with no response row used to vanish from the
        # result, leaving a "complete" assessment out of 20.
        evaluation, summary = self._assess(skip_orders=(5,))
        self.assertEqual(summary.metadata["unanswered_question_count"], 1)
        self.assertEqual(summary.metadata["unanswered_question_orders"], [5])
        self.assertEqual(summary.status, SessionEvaluationSummary.STATUS_PARTIALLY_EVALUATED)
        self.assertEqual(evaluation.readiness_status, "INCOMPLETE")

    def test_ai_scoring_never_writes_a_scheduled_assessments_outcome(self):
        # SCH-07 / GOV-02: the evaluator's outcome stands; the AI summary of
        # the transcript is reference only.
        def evaluator_outcome(evaluation):
            evaluation.score = Decimal("45.00")
            evaluation.readiness_status = "PARTIALLY_READY"
            evaluation.save(update_fields=["score", "readiness_status"])

        evaluation, summary = self._assess(scheduled=True, before_scoring=evaluator_outcome)
        self.assertEqual(evaluation.score, Decimal("45.00"))
        self.assertEqual(evaluation.readiness_status, "PARTIALLY_READY")
        self.assertIsNone(EvaluationReadinessRecordService.get_existing(evaluation))
        self.assertIsNotNone(summary.overall_percentage)

    def test_scheduled_not_ready_rating_gets_no_certificate(self):
        # CER-02: before this, a Not Ready evaluator outcome was certified.
        evaluation, summary = self._assess(scheduled=True)
        EvaluatorRating.objects.create(evaluation=evaluation, safety_awareness=20, hygiene=30, communication=30,
                                       task_execution=30, behavior_integrity=50, rated_by=self.owner)
        evaluation.refresh_from_db()
        self.assertEqual(certificate_eligibility(evaluation, summary), (False, "NOT_READY"))

    def test_scheduled_partially_ready_rating_stays_eligible(self):
        # CER-03: the existing Partially Ready treatment is kept.
        evaluation, summary = self._assess(scheduled=True)
        EvaluatorRating.objects.create(evaluation=evaluation, safety_awareness=50, hygiene=50, communication=50,
                                       task_execution=50, behavior_integrity=50, rated_by=self.owner)
        evaluation.refresh_from_db()
        self.assertEqual(certificate_eligibility(evaluation, summary), (True, "PARTIALLY_READY"))


class IndicatorWeightTests(SimpleTestCase):
    """FIX-02: whole-number rounding left the last point of 6- and 7-point
    lists at 0 and -1 (HK-TSK-002)."""

    def test_importer_weights_are_positive_and_sum_to_the_maximum(self):
        for count in (1, 3, 6, 7, 9):
            weights = _weighted_indicators([f"point {i}" for i in range(count)], 5)
            values = [Decimal(value) for value in weights.values()]
            self.assertTrue(all(value > 0 for value in values), weights)
            self.assertEqual(sum(values), Decimal("5"))

    def test_scorer_ignores_a_weight_map_with_zero_or_negative_weights(self):
        rule = ScoringRule(scoring_method="WEIGHTED_INDICATOR_MATCH")
        expected = ["a", "b", "c", "d", "e", "f", "g"]
        weighted = {key: Decimal("1") for key in expected[:-1]}
        weighted["g"] = Decimal("-1")
        score = Week6ScoringService._calculate_rule_score(
            rule=rule, matched=expected[:-1], expected=expected, weighted=weighted, max_score=Decimal("5"))
        # Equal weights: 6 of 7 points, never above the 5-point maximum.
        self.assertEqual(score, Decimal("4.29"))

    def test_valid_weights_are_still_used(self):
        rule = ScoringRule(scoring_method="WEIGHTED_INDICATOR_MATCH")
        weighted = {"a": Decimal("4"), "b": Decimal("1")}
        score = Week6ScoringService._calculate_rule_score(
            rule=rule, matched=["a"], expected=["a", "b"], weighted=weighted, max_score=Decimal("5"))
        self.assertEqual(score, Decimal("4.00"))


class ReportContentTests(SimpleTestCase):
    def _indicator(self, status, record_value=None):
        evaluation = mock.Mock(readiness_status=status)
        record = mock.Mock(readiness_indicator=record_value) if record_value else None
        with mock.patch.object(EvaluationReadinessRecordService, "get_correction", return_value=None):
            return EvaluationReportService._resolve_readiness_indicator(evaluation, record)

    def test_one_label_per_rating(self):
        # OUT-06
        self.assertEqual(self._indicator("READY"), {"value": "جاهز", "display": "Ready", "code": "READY", "level": 1})
        partial = self._indicator("PARTIALLY_READY")
        self.assertEqual((partial["value"], partial["display"]), ("جاهز جزئيًا", "Partially Ready"))
        not_ready = self._indicator("NOT_READY")
        self.assertEqual((not_ready["value"], not_ready["display"]), ("غير جاهز", "Not Ready"))

    def test_older_not_ready_wording_still_resolves(self):
        for stored in ("غير جاهز", "توجد فجوات جاهزية", "تم تحديد فجوات في الجاهزية"):
            self.assertEqual(self._indicator("", record_value=stored)["code"], "NOT_READY")

    def _risks(self, flags, competencies, evidence):
        return EvaluationReportService._build_risk_indicators(
            competency_breakdown=competencies, response_evidence_summary=evidence,
            human_review_flags=flags, critical_failures=[{"competency_code": "safety_awareness"}])

    def test_risks_are_listed_under_their_own_competency(self):
        # DOC-03: on 19 Oct the needle answer appeared under Integrity and
        # Safety read "no risk factors".
        competencies = [
            {"competency_code": "safety_awareness", "competency_name": "Safety Awareness", "percentage": 80,
             "status": "MEETS_THRESHOLD", "assessment_status": "MEETS_THRESHOLD"},
            {"competency_code": "behavior_integrity", "competency_name": "Behavior & Integrity", "percentage": 90,
             "status": "MEETS_THRESHOLD", "assessment_status": "MEETS_THRESHOLD"},
        ]
        evidence = [{"candidate_response_id": "r-safety", "competency_code": "safety_awareness"}]
        flags = [{"flag_type": "critical_failure", "candidate_response_id": "r-safety", "requires_review": True,
                  "message": "Possible safety or compliance concern: picks up needle by hand."}]
        risks = self._risks(flags, competencies, evidence)
        self.assertIn("Possible safety or compliance concern: picks up needle by hand.", risks["safety_risk"]["evidence"])
        self.assertEqual(risks["safety_risk"]["level"], "High")
        self.assertNotIn("Possible safety or compliance concern: picks up needle by hand.", risks["integrity_risk"]["evidence"])
        self.assertEqual(risks["integrity_risk"]["level"], "Low")

    def test_communication_below_threshold_is_never_low_risk(self):
        competencies = [{"competency_code": "communication_ability", "competency_name": "Communication Ability",
                         "percentage": 50, "status": "BELOW_THRESHOLD", "assessment_status": "BELOW_THRESHOLD"}]
        risk = self._risks([], competencies, [])["communication_risk"]
        self.assertNotEqual(risk["level"], "Low")
        self.assertGreaterEqual(risk["risk_score"], 50)

    def test_typed_interview_claims_no_audio_or_observation(self):
        # DOC-05 / AI-03
        summary = mock.Mock(total_response_count=21, evaluated_response_count=21)
        session = mock.Mock(task_observation_enabled=True)
        _, factors = EvaluationReportService._derive_evaluation_reliability(
            session=session, summary=summary, human_review_flags=[], response_evidence_summary=[])
        self.assertIn("Typed answers; no audio captured", factors)
        self.assertNotIn("Good audio quality", factors)
        self.assertNotIn("Practical observation included", factors)

    def test_methodology_claims_no_simulation_or_task_observation(self):
        text = json.dumps(EvaluationReportService._build_assessment_methodology())
        for claim in ("Simulation", "Speech Processing", "Task Observation"):
            self.assertNotIn(claim, text)
