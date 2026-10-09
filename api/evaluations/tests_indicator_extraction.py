"""Regression suite for Priority 9 indicator-ID evidence extraction
(handoff deliverable C; policies D-01, D-04, D-05).

Drives the real pipeline - approved v1.2 bank via the real importer, the
indicator registry, interpretation post-processing, rule-input preparation
and the unchanged Week 6 Rule Engine - for the nine Priority 9 questions in
English, Arabic and machine-translated Arabic. Only the LLM call is replaced
by a controlled extractor that answers with indicator IDs, so these tests
prove the deterministic policy and plumbing, not live-AI accuracy (QA-13
covers that in Staging).

Approved scoring methodology is deliberately unchanged: every Must Include is
still required by the current rule (D-02/D-03 await SME classification), and
no Negative Indicator creates a gate (D-04 severities are unassigned).
"""
import json
import tempfile
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.candidates.models import Candidate
from api.core.constants import EvaluationType, InterviewEvaluationTier, ReadinessStatus, Roles
from api.evaluations.models import Evaluation, ResponseEvaluationResult
from api.evaluations.scoring_services import Week6ScoringService
from api.interviews.management.commands.import_governance_question_bank import _split_points
from api.interviews.models import InterviewConfiguration
from api.questions.models import IndicatorDefinition, QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion
from api.translation.services import AIProcessingOrchestrationService, ResponseInterpretationService

PRIORITY_NINE = (
    "CCG-TSK-002", "CC-TEK-005", "ECG-TSK-001", "FDA-TSK-004", "HK-TSK-001",
    "IC-TEK-004", "CCG-TSK-003", "IC-TSK-003", "SNC-GEN-009",
)
LANGUAGES = ("EN", "AR", "AR_TRANSLATED")
VERSION = "GOV1.2-FINAL"
ID_MODE = {"INDICATOR_ID_EXTRACTION_ENABLED": True}


class ControlledExtractor:
    """Answers from the indicators offered in the prompt. `negative` is
    (index, polarity, attribution); `quote=False` drops quotes."""

    def __init__(self, *, must_include="all", negative=None, quote=True, uncertain=False, extra=None):
        self.must_include, self.negative, self.quote, self.uncertain = must_include, negative, quote, uncertain
        self.extra = extra or []
        self.prompts = []

    def interpret(self, *, prompt):
        payload = json.loads(prompt)
        self.prompts.append(payload)
        indicators = payload["question"].get("indicators") or []
        mi = [i for i in indicators if i["type"] == "must_include"]
        ni = [i for i in indicators if i["type"] == "negative"]
        chosen = mi if self.must_include == "all" else mi[:-1]
        observations = [
            {"indicator_id": i["id"], "polarity": "affirmative", "attribution": "self",
             "quote": "(candidate's words)" if self.quote else "", "source_language": "ar",
             "uncertain": self.uncertain}
            for i in chosen
        ]
        if self.negative is not None and ni:
            index, polarity, attribution = self.negative
            observations.append({"indicator_id": ni[index]["id"], "polarity": polarity, "attribution": attribution,
                                 "quote": "(candidate's words)", "source_language": "en", "uncertain": False})
        observations += self.extra
        body = {
            "answer_relevance": "high", "mentioned_steps": [], "missing_steps": [], "safety_risks": [],
            "compliance_risks": [], "language_quality": "clear", "extraction_confidence": 0.95,
            "confidence_notes": [], "uncertainty_notes": [], "transcript_issues": [], "key_evidence_phrases": [],
            "observations": observations,
        }
        return {"provider": "STUB", "model": "stub", "raw_content": json.dumps(body, ensure_ascii=False), "metadata": {}}


class IndicatorExtractionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_governance_question_bank", verbosity=0)
        call_command("load_indicator_registry", "--priority-nine", verbosity=0)
        cls.owner = User.objects.create_user(email="idx@example.com", password="x", first_name="I", last_name="D",
                                             role=Roles.B2B, is_verified=True)
        cls.company = Company.objects.create(name="Indicator QA", registration_number="IDX-1", company_size="11-50",
                                             phone_number="+1", country="X", city="Y", admin_user=cls.owner)
        CompanyEmployerProfile.objects.create(user=cls.owner, company_name=cls.company.name, company=cls.company,
                                              company_registration_number="IDX-1", company_size="11-50")

    def setUp(self):
        self.seq = 0

    def _score(self, code, lang, extractor):
        self.seq += 1
        template_lang = "EN" if lang == "EN" else "AR"
        template = QuestionTemplate.objects.get(question_code=code, language=template_lang, question_version=VERSION)
        config = InterviewConfiguration.objects.filter(
            role_code=template.role_code, language=template_lang, evaluation_tier=InterviewEvaluationTier.SCREENING,
        ).first()
        candidate = Candidate.objects.create(
            first_name=f"C{self.seq}", last_name="QA", email=f"idx-{self.seq}-{code}-{lang}@example.com",
            passport_id=f"IDX{self.seq:04d}{code}", job_role="OT", core_skills="x", preferred_language=template_lang,
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
        with mock.patch.object(ResponseInterpretationService, "get_provider", return_value=extractor):
            AIProcessingOrchestrationService.interpret_response(response=response)
        response.refresh_from_db()
        AIProcessingOrchestrationService.prepare_evaluation_input(response=response)
        response.refresh_from_db()
        Week6ScoringService.run_for_evaluation(evaluation=evaluation)
        evaluation.refresh_from_db()
        return ResponseEvaluationResult.objects.get(response=response), evaluation

    # -- registry --------------------------------------------------------

    def test_registry_has_the_69_priority_indicators_with_locked_english(self):
        self.assertEqual(IndicatorDefinition.objects.count(), 69)
        self.assertEqual(IndicatorDefinition.objects.filter(indicator_type=IndicatorDefinition.TYPE_MUST_INCLUDE).count(), 46)
        self.assertEqual(IndicatorDefinition.objects.filter(indicator_type=IndicatorDefinition.TYPE_NEGATIVE).count(), 23)
        ecg = list(IndicatorDefinition.objects.filter(question_code="ECG-TSK-001").values_list("indicator_id", "text_en"))
        self.assertIn(("ECG-TSK-001-MI-03", "checks balance"), ecg)
        self.assertIn(("ECG-TSK-001-NI-01", "Pulls abruptly"), ecg)
        # Severity and mandatory flags stay unassigned until SME sign-off.
        self.assertFalse(IndicatorDefinition.objects.exclude(severity="").exists())
        self.assertFalse(IndicatorDefinition.objects.exclude(mandatory=None).exists())

    def test_registry_reload_is_idempotent_and_never_changes_english(self):
        call_command("load_indicator_registry", "--priority-nine", verbosity=0)
        self.assertEqual(IndicatorDefinition.objects.count(), 69)
        IndicatorDefinition.objects.filter(indicator_id="ECG-TSK-001-MI-03").update(text_en="something else")
        with self.assertRaises(CommandError):
            call_command("load_indicator_registry", "--priority-nine", verbosity=0)

    def _draft_workbook(self, rows):
        path = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False).name
        wb = Workbook()
        ws = wb.active
        ws.append(["Indicator ID", "Question Code", "English (source locked)", "Arabic (draft)"])
        for row in rows:
            ws.append(row)
        wb.save(path)
        return path

    def test_draft_arabic_is_refused_unless_explicitly_allowed(self):
        path = self._draft_workbook([["ECG-TSK-001-MI-03", "ECG-TSK-001", "checks balance", "التحقق من توازن الشخص"]])
        with self.assertRaises(CommandError):
            call_command("load_indicator_registry", "--priority-nine", "--arabic-draft", path, verbosity=0)
        with override_settings(ALLOW_DRAFT_INDICATOR_TRANSLATIONS=True):
            call_command("load_indicator_registry", "--priority-nine", "--arabic-draft", path, verbosity=0)
        record = IndicatorDefinition.objects.get(indicator_id="ECG-TSK-001-MI-03")
        self.assertEqual(record.text_ar, "التحقق من توازن الشخص")
        self.assertEqual(record.text_ar_status, IndicatorDefinition.STATUS_DRAFT)

    def test_draft_arabic_with_different_english_is_rejected(self):
        path = self._draft_workbook([["ECG-TSK-001-MI-03", "ECG-TSK-001", "checks posture", "x"]])
        with override_settings(ALLOW_DRAFT_INDICATOR_TRANSLATIONS=True), self.assertRaises(CommandError):
            call_command("load_indicator_registry", "--priority-nine", "--arabic-draft", path, verbosity=0)

    # -- off by default ----------------------------------------------------

    def test_feature_off_leaves_interpretation_exactly_as_before(self):
        extractor = ControlledExtractor()
        self._score("ECG-TSK-001", "EN", extractor)
        question = extractor.prompts[0]["question"]
        self.assertNotIn("indicators", question)
        self.assertNotIn("observations", extractor.prompts[0]["instruction"])

    # -- parity ------------------------------------------------------------

    @override_settings(**ID_MODE)
    def test_complete_evidence_scores_full_marks_in_every_language(self):
        for code in PRIORITY_NINE:
            for lang in LANGUAGES:
                with self.subTest(code=code, lang=lang):
                    result, _ = self._score(code, lang, ControlledExtractor())
                    self.assertEqual(result.score, result.max_score)
                    self.assertFalse(result.critical_failure)
                    self.assertFalse(result.requires_human_review)

    @override_settings(**ID_MODE)
    def test_english_and_arabic_produce_identical_evidence_and_results(self):
        cases = ({}, {"must_include": "all_but_last"}, {"negative": (0, "affirmative", "self")},
                 {"negative": (0, "negated", "self")})
        for code in PRIORITY_NINE:
            for kwargs in cases:
                outcome = {}
                for lang in LANGUAGES:
                    result, evaluation = self._score(code, lang, ControlledExtractor(**kwargs))
                    artifact = result.response.evaluation_input_artifact
                    decisions = sorted((o["indicator_id"], o["decision"]) for o in artifact.metadata["indicator_observations"])
                    outcome[lang] = (result.score, sorted(result.matched_indicators), result.critical_failure,
                                     result.requires_human_review, evaluation.readiness_status, decisions)
                with self.subTest(code=code, case=kwargs):
                    self.assertEqual(outcome["EN"], outcome["AR"])
                    self.assertEqual(outcome["EN"], outcome["AR_TRANSLATED"])

    @override_settings(**ID_MODE)
    def test_one_missing_must_include_keeps_the_current_approved_rule(self):
        # D-02/D-03 classifications are not yet SME-approved: the current rule
        # (every Must Include required) is deliberately unchanged.
        result, _ = self._score("ECG-TSK-001", "AR", ControlledExtractor(must_include="all_but_last"))
        self.assertEqual(result.score, Decimal("0"))
        self.assertEqual(result.missing_indicators, ["moves slowly"])

    # -- negative indicators and polarity (D-04) ---------------------------

    @override_settings(**ID_MODE)
    def test_affirmative_unsafe_act_is_recorded_for_human_review_without_a_gate(self):
        for code in PRIORITY_NINE:
            with self.subTest(code=code):
                result, evaluation = self._score(code, "AR", ControlledExtractor(negative=(0, "affirmative", "self")))
                artifact = result.response.evaluation_input_artifact
                self.assertFalse(result.critical_failure)
                self.assertTrue(result.requires_human_review)
                self.assertEqual(artifact.metadata["negative_evidence_ids"], [f"{code}-NI-01"])
                self.assertNotEqual(evaluation.readiness_status, ReadinessStatus.NOT_READY)
                # D-05: the question score is not rewritten.
                self.assertEqual(result.score, result.max_score)

    @override_settings(**ID_MODE)
    def test_negated_hypothetical_quoted_and_other_person_never_count(self):
        for polarity, attribution in (("negated", "self"), ("hypothetical", "self"), ("quoted", "self"),
                                      ("affirmative", "other")):
            with self.subTest(polarity=polarity, attribution=attribution):
                result, _ = self._score("CC-TEK-005", "EN", ControlledExtractor(negative=(1, polarity, attribution)))
                artifact = result.response.evaluation_input_artifact
                self.assertEqual(artifact.metadata["negative_evidence_ids"], [])
                self.assertFalse(result.requires_human_review)
                decision = next(o for o in artifact.metadata["indicator_observations"] if o["indicator_id"] == "CC-TEK-005-NI-02")
                self.assertEqual(decision["decision"], "context_only_no_effect")

    @override_settings(**ID_MODE)
    def test_uncertain_or_unquoted_evidence_goes_to_review_and_does_not_count(self):
        result, _ = self._score("HK-TSK-001", "AR", ControlledExtractor(uncertain=True))
        self.assertTrue(result.requires_human_review)
        self.assertEqual(result.score, Decimal("0"))
        result, _ = self._score("HK-TSK-001", "AR", ControlledExtractor(quote=False))
        self.assertTrue(result.requires_human_review)
        self.assertEqual(result.matched_indicators, [])

    @override_settings(**ID_MODE)
    def test_unknown_indicator_ids_are_ignored_and_audited(self):
        stray = {"indicator_id": "HK-TSK-001-NI-99", "polarity": "affirmative", "attribution": "self",
                 "quote": "x", "source_language": "en", "uncertain": False}
        result, _ = self._score("HK-TSK-001", "EN", ControlledExtractor(extra=[stray]))
        artifact = result.response.evaluation_input_artifact
        decision = next(o for o in artifact.metadata["indicator_observations"] if o["indicator_id"] == "HK-TSK-001-NI-99")
        self.assertEqual(decision["decision"], "ignored_unknown_indicator")
        self.assertEqual(result.score, result.max_score)

    @override_settings(**ID_MODE)
    def test_step_labels_count_when_the_interpreter_omits_observations(self):
        # Seen in live testing: observations empty, required points correctly
        # labelled in mentioned_steps - must not silently score zero.
        class LabelsOnly(ControlledExtractor):
            def interpret(self, *, prompt):
                out = super().interpret(prompt=prompt)
                body = json.loads(out["raw_content"])
                body["mentioned_steps"] = json.loads(prompt)["question"]["expected_steps"]
                body["observations"] = []
                out["raw_content"] = json.dumps(body)
                return out

        for lang in ("EN", "AR"):
            with self.subTest(lang=lang):
                result, _ = self._score("ECG-TSK-001", lang, LabelsOnly())
                self.assertEqual(result.score, result.max_score)

    @override_settings(**ID_MODE)
    def test_a_non_affirmative_observation_overrides_the_step_label(self):
        class NegatedButLabelled(ControlledExtractor):
            def interpret(self, *, prompt):
                out = super().interpret(prompt=prompt)
                body = json.loads(out["raw_content"])
                body["mentioned_steps"] = json.loads(prompt)["question"]["expected_steps"]
                for o in body["observations"]:
                    if o["indicator_id"].endswith("MI-03"):
                        o["polarity"] = "negated"
                out["raw_content"] = json.dumps(body)
                return out

        result, _ = self._score("ECG-TSK-001", "EN", NegatedButLabelled())
        self.assertEqual(result.score, 0)
        self.assertIn("checks balance", result.missing_indicators)

    # -- Arabic text gating (D-01) ------------------------------------------

    @override_settings(**ID_MODE)
    def test_draft_arabic_text_reaches_the_prompt_only_when_allowed(self):
        IndicatorDefinition.objects.filter(indicator_id="ECG-TSK-001-MI-03").update(
            text_ar="التحقق من توازن الشخص", text_ar_status=IndicatorDefinition.STATUS_DRAFT)
        extractor = ControlledExtractor()
        self._score("ECG-TSK-001", "AR", extractor)
        item = next(i for i in extractor.prompts[0]["question"]["indicators"] if i["id"] == "ECG-TSK-001-MI-03")
        self.assertNotIn("text_ar", item)
        with override_settings(ALLOW_DRAFT_INDICATOR_TRANSLATIONS=True):
            extractor = ControlledExtractor()
            self._score("ECG-TSK-001", "AR", extractor)
        item = next(i for i in extractor.prompts[0]["question"]["indicators"] if i["id"] == "ECG-TSK-001-MI-03")
        self.assertEqual(item["text_ar"], "التحقق من توازن الشخص")

    @override_settings(**ID_MODE)
    def test_audit_trail_and_prompt_version(self):
        result, _ = self._score("FDA-TSK-004", "AR", ControlledExtractor(negative=(1, "affirmative", "self")))
        artifact = result.response.evaluation_input_artifact
        self.assertEqual(artifact.metadata["indicator_mode"], "indicator_ids")
        self.assertEqual(len(artifact.metadata["indicator_registry"]), 8)
        self.assertTrue(all(o["quote"] for o in artifact.metadata["indicator_observations"]))
        self.assertTrue(result.response.ai_interpretation.prompt_version.endswith("+indicator-ids-v1"))

    # -- parser and locks -----------------------------------------------------

    def test_arabic_semicolon_is_a_separator_and_english_parsing_is_unchanged(self):
        self.assertEqual(_split_points("أ؛ ب؛ ج"), ["أ", "ب", "ج"])
        self.assertEqual(_split_points("a; b, c; d"), ["a", "b, c", "d"])
        self.assertEqual(_split_points("a, b"), ["a", "b"])

    def test_session_lengths_unchanged(self):
        for tier, expected in ((InterviewEvaluationTier.SCREENING, 7), (InterviewEvaluationTier.FULL, 21)):
            counts = set(InterviewConfiguration.objects.filter(question_set_version=VERSION, evaluation_tier=tier)
                         .values_list("total_questions", flat=True))
            self.assertEqual(counts, {expected})
