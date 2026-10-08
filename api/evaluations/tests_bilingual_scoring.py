"""Regression suite for bilingual scoring and critical safety gates.

Drives the real pipeline - the approved v1.2 bank imported by the real
command, interpretation post-processing, rule-input preparation and the
Week 6 Rule Engine - for the 9 Priority Blueprint QA Register questions, in
English, Arabic and machine-translated Arabic. Only the LLM call is replaced,
by a stub that answers from the closed vocabularies in the prompt, so any
failure here is a rules/data defect rather than model variance.

Methodology is deliberately NOT changed by these fixes and is pinned here:
every Must Include item stays mandatory (one missing -> 0), thresholds and
criticality come from the approved bank, and sessions keep their lengths.
"""
import json
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.candidates.models import Candidate
from api.core.constants import EvaluationType, InterviewEvaluationTier, ReadinessStatus, Roles
from api.evaluations.models import CompetencyEvaluationResult, Evaluation, ResponseEvaluationResult
from api.evaluations.scoring_services import Week6ScoringService
from api.interviews.management.commands.import_governance_question_bank import _split_points
from api.interviews.models import InterviewConfiguration
from api.questions.models import QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion
from api.translation.services import AIProcessingOrchestrationService, ResponseInterpretationService

PRIORITY_QUESTIONS = (
    "CCG-TSK-002", "CC-TEK-005", "ECG-TSK-001", "FDA-TSK-004", "HK-TSK-001",
    "IC-TEK-004", "CCG-TSK-003", "IC-TSK-003", "SNC-GEN-009",
)
LANGUAGES = ("EN", "AR", "AR_TRANSLATED")
VERSION = "GOV1.2-FINAL"


class PromptDrivenStub:
    """Answers like a perfect interpreter, but only from what the prompt gives
    it - so a vocabulary the Rule Engine can't match fails the test."""

    def __init__(self, *, mention="all", negative=None, quote=True, free_text_risk=None):
        self.mention = mention
        self.negative = negative  # index into question.negative_indicators
        self.quote = quote
        self.free_text_risk = free_text_risk
        self.prompts = []

    def interpret(self, *, prompt):
        payload = json.loads(prompt)
        self.prompts.append(payload)
        steps = payload["question"]["expected_steps"]
        negatives = payload["question"].get("negative_indicators") or []
        mentioned = steps if self.mention == "all" else steps[:-1]
        body = {
            "answer_relevance": "high",
            "mentioned_steps": mentioned,
            "missing_steps": [s for s in steps if s not in mentioned],
            "safety_risks": [self.free_text_risk] if self.free_text_risk else [],
            "compliance_risks": [],
            "language_quality": "clear",
            "extraction_confidence": 0.95,
            "confidence_notes": [], "uncertainty_notes": [], "transcript_issues": [], "key_evidence_phrases": [],
            "observed_negative_indicators": [],
            "negative_indicator_evidence": [],
        }
        if self.negative is not None and negatives:
            indicator = negatives[self.negative]
            body["observed_negative_indicators"] = [indicator]
            body["negative_indicator_evidence"] = [
                {"indicator": indicator, "quote": "(candidate's own words)" if self.quote else ""}
            ]
        return {"provider": "STUB", "model": "stub", "raw_content": json.dumps(body, ensure_ascii=False), "metadata": {}}


class BilingualScoringRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_governance_question_bank", verbosity=0)
        cls.owner = User.objects.create_user(
            email="bilingual-qa@example.com", password="x", first_name="QA", last_name="Owner",
            role=Roles.B2B, is_verified=True,
        )
        cls.company = Company.objects.create(
            name="Bilingual QA", registration_number="BQA-1", company_size="11-50", phone_number="+1",
            country="X", city="Y", admin_user=cls.owner,
        )
        CompanyEmployerProfile.objects.create(
            user=cls.owner, company_name=cls.company.name, company=cls.company,
            company_registration_number=cls.company.registration_number, company_size=cls.company.company_size,
        )

    def setUp(self):
        self._seq = 0

    # -- helpers --------------------------------------------------------

    def _score(self, code, lang, stub):
        self._seq += 1
        template_lang = "EN" if lang == "EN" else "AR"
        template = QuestionTemplate.objects.get(question_code=code, language=template_lang, question_version=VERSION)
        config = InterviewConfiguration.objects.filter(
            role_code=template.role_code, language=template_lang, evaluation_tier=InterviewEvaluationTier.SCREENING,
        ).first()
        candidate = Candidate.objects.create(
            first_name=f"C{self._seq}", last_name="QA", email=f"bqa-{code}-{lang}-{self._seq}@example.com",
            passport_id=f"BQA{self._seq:04d}{code}", job_role="OT", core_skills="x", preferred_language=template_lang,
            passport_document="candidates/documents/passport/test.pdf", created_by=self.owner, company=self.company,
        )
        session = InterviewSession.objects.create(
            candidate=candidate, organization=self.company, config=config, role_name=template.role_name,
            role_code=template.role_code, ui_language="EN", candidate_language=template_lang,
            tts_language_code="en-US" if template_lang == "EN" else "ar-SA",
            stt_language_code="en-US" if template_lang == "EN" else "ar-SA",
            total_questions=1, evaluation_tier=InterviewEvaluationTier.SCREENING,
            rubric_version=VERSION, question_set_version=VERSION,
            expires_at=InterviewSession.build_expiry(30), created_by=self.owner,
        )
        question = SessionQuestion.objects.create(
            session=session, question_template=template, question_text=template.question_text, question_order=1,
            skill_tag=template.skill_tag, skill=template.skill, domain=template.domain,
        )
        response = CandidateResponse.objects.create(
            session=session, question=question, transcript="(answer)", original_transcript="(answer)",
            transcript_language="en" if template_lang == "EN" else "ar",
        )
        if lang == "AR_TRANSLATED":
            response.translated_transcript = "(answer, machine-translated)"
            response.translation_status = "COMPLETED"
            response.save()
        evaluation = Evaluation.objects.create(
            session=session, candidate=candidate, evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now(), duration_minutes=30, created_by=self.owner, company=self.company,
            evaluation_tier=InterviewEvaluationTier.SCREENING,
        )
        with mock.patch.object(ResponseInterpretationService, "get_provider", return_value=stub):
            AIProcessingOrchestrationService.interpret_response(response=response)
        response.refresh_from_db()
        AIProcessingOrchestrationService.prepare_evaluation_input(response=response)
        response.refresh_from_db()
        Week6ScoringService.run_for_evaluation(evaluation=evaluation)
        evaluation.refresh_from_db()
        return ResponseEvaluationResult.objects.get(response=response), evaluation

    # -- Arabic parsing -------------------------------------------------

    def test_arabic_semicolon_splits_must_include(self):
        self.assertEqual(_split_points("أ؛ ب؛ ج"), ["أ", "ب", "ج"])
        # English behaviour unchanged.
        self.assertEqual(_split_points("a; b, c; d"), ["a", "b, c", "d"])
        self.assertEqual(_split_points("a, b"), ["a", "b"])

    def test_imported_arabic_templates_have_multiple_steps(self):
        single = [
            t.question_code for t in QuestionTemplate.objects.filter(language="AR", question_version=VERSION)
            if len(t.expected_steps) < 2
        ]
        self.assertEqual(single, [])

    def test_repair_command_resplits_legacy_single_step_templates(self):
        template = QuestionTemplate.objects.get(question_code="ECG-TSK-001", language="AR", question_version=VERSION)
        joined = "؛ ".join(template.expected_steps)
        QuestionTemplate.objects.filter(pk=template.pk).update(expected_steps=[joined])

        call_command("repair_arabic_expected_steps", verbosity=0)  # dry run
        template.refresh_from_db()
        self.assertEqual(len(template.expected_steps), 1)

        call_command("repair_arabic_expected_steps", "--apply", verbosity=0)
        template.refresh_from_db()
        self.assertEqual(len(template.expected_steps), 6)

    # -- English / Arabic parity -----------------------------------------

    def test_prompt_uses_english_rule_vocabulary_for_arabic_answers(self):
        stub = PromptDrivenStub()
        self._score("ECG-TSK-001", "AR", stub)
        question = stub.prompts[0]["question"]
        self.assertEqual(question["expected_steps"],
                         ["Positions self correctly", "supports weight properly", "checks balance", "moves slowly"])
        self.assertEqual(question["negative_indicators"], ["Pulls abruptly", "no balance check"])
        self.assertTrue(question["local_expected_steps"])  # Arabic blueprint passed as context only

    def test_complete_answers_score_full_marks_in_every_language(self):
        for code in PRIORITY_QUESTIONS:
            for lang in LANGUAGES:
                with self.subTest(code=code, lang=lang):
                    result, _ = self._score(code, lang, PromptDrivenStub())
                    self.assertEqual(result.score, result.max_score)
                    self.assertTrue(result.passed_required_indicators)
                    self.assertFalse(result.critical_failure)

    def test_incomplete_answers_keep_the_approved_mandatory_rule_in_every_language(self):
        # Methodology unchanged: every Must Include item is mandatory.
        for code in PRIORITY_QUESTIONS:
            for lang in LANGUAGES:
                with self.subTest(code=code, lang=lang):
                    result, _ = self._score(code, lang, PromptDrivenStub(mention="all_but_last"))
                    self.assertEqual(result.score, Decimal("0"))
                    self.assertEqual(len(result.missing_indicators), 1)
                    self.assertIn("Required indicators missing", result.explanation)

    def test_english_and_arabic_produce_identical_results(self):
        for code in PRIORITY_QUESTIONS:
            for stub_kwargs in ({}, {"mention": "all_but_last"}, {"negative": 0}):
                outcomes = {}
                for lang in LANGUAGES:
                    result, evaluation = self._score(code, lang, PromptDrivenStub(**stub_kwargs))
                    outcomes[lang] = (result.score, result.max_score, sorted(result.matched_indicators),
                                      result.critical_failure, evaluation.readiness_status)
                with self.subTest(code=code, case=stub_kwargs):
                    self.assertEqual(outcomes["EN"], outcomes["AR"])
                    self.assertEqual(outcomes["EN"], outcomes["AR_TRANSLATED"])

    # -- critical safety failures ----------------------------------------

    def test_quoted_negative_indicator_is_a_critical_failure_in_every_language(self):
        for code in PRIORITY_QUESTIONS:
            for lang in LANGUAGES:
                with self.subTest(code=code, lang=lang):
                    result, evaluation = self._score(code, lang, PromptDrivenStub(negative=0))
                    self.assertTrue(result.critical_failure)
                    # Stored score now agrees with the gate; the pre-gate score is kept for audit.
                    self.assertEqual(result.score, Decimal("0"))
                    self.assertEqual(result.percentage, Decimal("0"))
                    self.assertEqual(Decimal(result.metadata["raw_score"]), result.max_score)
                    self.assertEqual(result.metadata["effective_score"], "0.00")
                    self.assertTrue(result.metadata["critical_failure_indicators_hit"])
                    self.assertEqual(evaluation.readiness_status, ReadinessStatus.NOT_READY)
                    self.assertIn(code, evaluation.readiness_override_reason)
                    competency = CompetencyEvaluationResult.objects.get(evaluation=evaluation)
                    self.assertEqual(competency.total_score, Decimal("0"))

    def test_negative_indicator_without_quote_goes_to_human_review_not_failure(self):
        result, evaluation = self._score("ECG-TSK-001", "AR", PromptDrivenStub(negative=0, quote=False))
        self.assertFalse(result.critical_failure)
        self.assertTrue(result.requires_human_review)
        self.assertNotEqual(evaluation.readiness_status, ReadinessStatus.NOT_READY)

    def test_free_text_risk_alone_never_invents_a_critical_failure(self):
        result, _ = self._score("ECG-TSK-001", "EN", PromptDrivenStub(free_text_risk="candidate seemed rushed"))
        self.assertFalse(result.critical_failure)
        self.assertEqual(result.score, result.max_score)

    def test_negative_indicator_outside_the_approved_list_is_ignored(self):
        class OffListStub(PromptDrivenStub):
            def interpret(self, *, prompt):
                out = super().interpret(prompt=prompt)
                body = json.loads(out["raw_content"])
                body["observed_negative_indicators"] = ["invented unsafe act"]
                body["negative_indicator_evidence"] = [{"indicator": "invented unsafe act", "quote": "x"}]
                out["raw_content"] = json.dumps(body)
                return out

        result, _ = self._score("ECG-TSK-001", "EN", OffListStub())
        self.assertFalse(result.critical_failure)
        interpretation = result.response.ai_interpretation
        self.assertEqual(interpretation.normalized_indicators["unmatched_negative_phrases"], ["invented unsafe act"])

    def test_audit_trail_records_vocabulary_and_evidence(self):
        result, _ = self._score("HK-TSK-001", "AR", PromptDrivenStub(negative=1))
        artifact = result.response.evaluation_input_artifact
        self.assertEqual(artifact.metadata["indicator_vocabulary_source"], "english_template")
        self.assertEqual(artifact.metadata["observed_negative_indicators"], ["no rinse"])
        self.assertEqual(artifact.metadata["negative_indicator_evidence"][0]["indicator"], "no rinse")
        self.assertTrue(artifact.metadata["negative_indicator_rule_id"])

    # -- methodology safeguards -------------------------------------------

    def test_session_lengths_unchanged(self):
        for tier, expected in ((InterviewEvaluationTier.SCREENING, 7), (InterviewEvaluationTier.FULL, 21)):
            counts = set(
                InterviewConfiguration.objects.filter(question_set_version=VERSION, evaluation_tier=tier)
                .values_list("total_questions", flat=True)
            )
            self.assertEqual(counts, {expected})
