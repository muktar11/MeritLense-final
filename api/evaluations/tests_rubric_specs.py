"""Management decisions of 20 Oct 2026 on AI-path scoring (docs/ASSESSMENT_RULES.md):
1. each question scored by its own approved rubric, including partial credit
   and the 70 legacy exceptions;
2. no Must Include point mandatory unless an approved specification says so
   (none of the 89 D-02 drafts applies); an affirmed unsafe action is scored
   by the rules, not routed to review;
5. one fixed competency pass mark (60% unless the role profile carries an
   approved exception), whichever questions are drawn.

The LLM is replaced by a controlled extractor; everything else runs through
the production code on the GOV2.0-RC1 bank."""
import json
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.candidates.models import Candidate
from api.contracts.models import Agreement
from api.core.constants import AgreementMethod, AgreementType, EvaluationType, InterviewEvaluationTier, ReadinessStatus, Roles
from api.evaluations import rubric_specs
from api.evaluations.certificate_services import certificate_eligibility
from api.evaluations.models import CompetencyEvaluationResult, Evaluation, SessionEvaluationSummary
from api.evaluations.scoring_services import Week6ScoringService
from api.interviews.management.commands.import_question_bank_draft import split_points
from api.interviews.models import InterviewConfiguration, QuestionBankVersion
from api.sessions.models import CandidateResponse, InterviewSession
from api.sessions.services import QuestionGenerationService
from api.translation.services import AIProcessingOrchestrationService, ResponseInterpretationService

BANK = Path(__file__).resolve().parents[1] / "interviews" / "fixtures" / "question_bank"
V2 = json.loads((BANK / "governance_v2_0.json").read_text(encoding="utf-8"))
SPECS = json.loads((BANK / "rubric_specs_gov2_0.json").read_text(encoding="utf-8"))
V2_BY_CODE = {q["question_code"]: q for q in V2["questions"]}
DRAFT = "GOV2.0-RC1"


def run(code, matched, negatives=(), total=None):
    _, spec = rubric_specs.spec_for(DRAFT, code)
    q = V2_BY_CODE[code]
    total = total if total is not None else len(split_points(q["must_include_en"]))
    from api.interviews.management.commands.import_governance_question_bank import _scoring_shape

    return rubric_specs.execute(spec, max_score=_scoring_shape(q["score_note"])["max_score"],
                                matched_ordinals=matched, total_points=total, negative_ordinals=negatives)


class RubricExecutionTests(SimpleTestCase):
    def test_every_question_in_the_bank_has_an_executable_rubric(self):
        for code in V2_BY_CODE:
            version, spec = rubric_specs.spec_for(DRAFT, code)
            self.assertEqual(version, SPECS["spec_version"])
            self.assertIn(spec["kind"], ("generic_530", "bands", "completion"), code)
        self.assertEqual(len(SPECS["questions"]), 74)  # 70 legacy + 4 question-specific 5/3/0

    def test_generic_note_full_and_one_missing_point(self):
        code = "HK-TSK-002"  # generic note, 7 points, carries a D-02 mandatory draft
        self.assertEqual(run(code, range(1, 8))["score"], Decimal("5.00"))
        one_missing = run(code, [1, 2, 3, 4, 5, 6])
        self.assertEqual((one_missing["score"], one_missing["band"]), (Decimal("3.00"), rubric_specs.BAND_PARTIAL))

    def test_d02_mandatory_draft_does_not_zero_an_answer(self):
        # "Use gloves/PPE as required" is point 1 of HK-TSK-002 and one of its
        # 89 unapproved D-02 drafts: missing it alone is a partial answer.
        result = run("HK-TSK-002", [2, 3, 4, 5, 6, 7])
        self.assertEqual(result["score"], Decimal("3.00"))
        self.assertFalse(result["critical_failure"])

    def test_two_or_more_missing_on_the_generic_note_is_marked_interim(self):
        result = run("HK-TSK-002", [1, 2, 3, 4, 5])
        self.assertEqual(result["band"], rubric_specs.BAND_MULTI_OMISSION)
        self.assertEqual(result["status"], "INTERIM_PENDING_DECISION")
        self.assertEqual(result["score"], Decimal(str(SPECS["generic_530"]["multi_omission_score"])).quantize(Decimal("0.01")))

    def test_affirmed_negative_is_a_zero_and_critical_failure(self):
        result = run("HK-TSK-002", range(1, 8), negatives=[1])
        self.assertEqual((result["score"], result["critical_failure"]), (Decimal("0.00"), True))

    def test_hk_saf_001_partial_case_fix_01(self):
        # FIX-01: "5: all 4 | 3: 2-3 | 0: <2".
        self.assertEqual(run("HK-SAF-001", [1, 2, 3, 4])["score"], Decimal("5.00"))
        self.assertEqual(run("HK-SAF-001", [1, 3, 4])["score"], Decimal("3.00"))
        self.assertEqual(run("HK-SAF-001", [2, 4])["score"], Decimal("3.00"))
        self.assertEqual(run("HK-SAF-001", [4])["score"], Decimal("0.00"))

    def test_hk_pro_001_legacy_two_point_band_fix_01(self):
        # FIX-01 / SCR-05: "3: all 3 | 2: calm only | 0: refusal" - max 3.
        self.assertEqual(run("HK-PRO-001", [1, 2, 3])["score"], Decimal("3.00"))
        self.assertEqual(run("HK-PRO-001", [1])["score"], Decimal("2.00"))
        self.assertEqual(run("HK-PRO-001", [1], negatives=[2])["score"], Decimal("0.00"))

    def test_hk_tsk_001_completion_mapping_fix_02(self):
        # FIX-02: 3 of 6 steps is 5/10, not 6/10, and never a mandatory zero.
        self.assertEqual(run("HK-TSK-001", [1, 2, 3])["score"], Decimal("5.00"))
        self.assertEqual(run("HK-TSK-001", range(1, 7))["score"], Decimal("10.00"))
        self.assertEqual(run("CCG-TSK-002", [1, 2, 3])["score"], Decimal("7.50"))

    def test_legacy_0_5_question_needs_only_its_named_elements(self):
        # "5: stops+reports": a concise answer that stops and reports gets 5
        # without the other two Must Include points; without reporting it is 0.
        self.assertEqual(run("HH-SHK-003", [1, 2])["score"], Decimal("5.00"))
        self.assertEqual(run("HH-SHK-003", [1, 3, 4])["score"], Decimal("0.00"))
        self.assertEqual(run("SNC-AUT-001", range(1, 7))["score"], Decimal("5.00"))
        self.assertEqual(run("SNC-AUT-001", range(1, 6))["score"], Decimal("0.00"))

    def test_note_specific_negative_band_is_not_a_critical_failure(self):
        # HK-BEH-001 "3: delayed": the "delays reporting" negative caps at 3.
        delayed = run("HK-BEH-001", [2, 3], negatives=[2])
        self.assertEqual((delayed["score"], delayed["critical_failure"]), (Decimal("3.00"), False))
        hides = run("HK-BEH-001", [2, 3], negatives=[1])
        self.assertEqual((hides["score"], hides["critical_failure"]), (Decimal("0.00"), True))
        self.assertEqual(run("HK-BEH-001", [1, 2])["score"], Decimal("5.00"))

    def test_more_steps_never_score_lower(self):
        # SNC-BEH-001 "5: all steps | 3: 2-3 steps": 4 or 5 steps keep the 3.
        self.assertEqual(run("SNC-BEH-001", [1, 2, 3, 4, 5])["score"], Decimal("3.00"))


class SubsetExtractor:
    """Affirms, per question, the Must Include and Negative Indicator
    ordinals `plan(question_code)` returns."""

    def __init__(self, plan):
        self.plan = plan

    def interpret(self, *, prompt):
        indicators = json.loads(prompt)["question"].get("indicators") or []
        code = indicators[0]["id"].removeprefix("V2-").rsplit("-", 2)[0] if indicators else ""
        mi_ordinals, ni_ordinals = self.plan(code)
        observations = []
        for indicator in indicators:
            kind, ordinal = indicator["id"].rsplit("-", 2)[1:]
            if (kind == "MI" and int(ordinal) in mi_ordinals) or (kind == "NI" and int(ordinal) in ni_ordinals):
                observations.append({"indicator_id": indicator["id"], "polarity": "affirmative", "attribution": "self",
                                     "quote": "(words)", "source_language": "en", "uncertain": False})
        body = {"answer_relevance": "high", "mentioned_steps": [], "missing_steps": [], "safety_risks": [],
                "compliance_risks": [], "language_quality": "clear", "extraction_confidence": 0.95,
                "confidence_notes": [], "uncertainty_notes": [], "transcript_issues": [], "key_evidence_phrases": [],
                "observations": observations}
        return {"provider": "STUB", "model": "stub", "raw_content": json.dumps(body), "metadata": {}}


def all_points(code):
    return set(range(1, len(split_points(V2_BY_CODE[code]["must_include_en"])) + 1)), set()


class RubricScoringJourneyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_governance_question_bank", verbosity=0)
        call_command("import_question_bank_draft", verbosity=0)
        call_command("activate_question_bank_version", DRAFT, verbosity=0)
        cls.owner = User.objects.create_user(email="rs@example.com", password="x", first_name="R", last_name="S",
                                             role=Roles.B2B, is_verified=True)
        cls.company = Company.objects.create(name="RS Co", registration_number="RS-1", company_size="11-50",
                                             phone_number="+1", country="X", city="Y", admin_user=cls.owner)
        CompanyEmployerProfile.objects.create(user=cls.owner, company_name=cls.company.name, company=cls.company,
                                              company_registration_number="RS-1", company_size="11-50")

    def setUp(self):
        self.seq = 0

    def _assess(self, role_code, plan, lang="EN"):
        self.seq += 1
        tier = InterviewEvaluationTier.FULL
        config = InterviewConfiguration.objects.filter(role_code=role_code, language=lang,
                                                       evaluation_tier=tier).order_by("-id").first()
        candidate = Candidate.objects.create(first_name="C", last_name=str(self.seq), email=f"rs-{self.seq}@example.com",
                                             passport_id=f"RS{self.seq}", job_role="OT", core_skills="",
                                             preferred_language=lang, passport_document="candidates/documents/passport/t.pdf",
                                             created_by=self.owner, company=self.company)
        consent = Agreement.objects.create(user=self.owner, agreement_type=AgreementType.CANDIDATE_CONSENT, version="1",
                                           method=AgreementMethod.CHECKBOX, status="SIGNED", accepted_at=timezone.now())
        session = InterviewSession.objects.create(
            candidate=candidate, organization=self.company, config=config, role_name=config.role_name,
            role_code=role_code, ui_language="EN", candidate_language=lang, tts_language_code="en-US",
            stt_language_code="en-US" if lang == "EN" else "ar-SA", evaluation_tier=tier,
            readiness_indicator_enabled=True, certificate_enabled=True, rubric_version=DRAFT,
            question_set_version=DRAFT, identity_verified=True, candidate_consent_agreement=consent,
            status="COMPLETED", ended_at=timezone.now(), expires_at=InterviewSession.build_expiry(30),
            created_by=self.owner)
        questions = QuestionGenerationService.generate_questions(session)
        evaluation = Evaluation.objects.create(
            session=session, candidate=candidate, evaluation_type=EvaluationType.INTERVIEW, status="COMPLETED",
            scheduled_date=timezone.now(), duration_minutes=45, created_by=self.owner, company=self.company,
            evaluation_tier=tier, readiness_indicator_enabled=True, certificate_enabled=True)
        with mock.patch.object(ResponseInterpretationService, "get_provider", return_value=SubsetExtractor(plan)):
            for q in questions:
                r = CandidateResponse.objects.create(session=session, question=q, transcript="(answer)",
                                                     original_transcript="(answer)", transcript_language=lang.lower())
                AIProcessingOrchestrationService.interpret_response(response=r)
                r.refresh_from_db()
                AIProcessingOrchestrationService.prepare_evaluation_input(response=r)
        summary = Week6ScoringService.run_for_evaluation(evaluation=evaluation)
        evaluation.refresh_from_db()
        return evaluation, summary, [q.question_template.question_code for q in questions]

    def test_one_missing_point_everywhere_earns_partial_credit_and_partially_ready_or_ready(self):
        def one_missing(code):
            points, _ = all_points(code)
            return points - {max(points)}, set()

        for lang in ("EN", "AR"):
            with self.subTest(lang=lang):
                evaluation, summary, codes = self._assess("hotel_housekeeper", one_missing, lang)
                self.assertEqual(summary.status, SessionEvaluationSummary.STATUS_EVALUATED)
                self.assertGreater(summary.total_score, 0)
                results = {r.question.question_template.question_code: r for r in evaluation.response_results.all()}
                for code in codes:
                    if code not in SPECS["questions"]:
                        self.assertEqual(results[code].score, Decimal("3.00"), code)
                        self.assertEqual(results[code].metadata["rubric_band"], rubric_specs.BAND_PARTIAL)
                # A routine partial answer never raises a review flag.
                self.assertFalse(any(r.requires_human_review for r in results.values()))
                self.assertTrue(all(r.passed_required_indicators for r in results.values()))

    def test_affirmed_unsafe_action_is_not_ready_without_review(self):
        target = {}

        def plan(code):
            points, _ = all_points(code)
            if not target and V2_BY_CODE[code]["competency_code"] == "safety_awareness" and code not in SPECS["questions"]:
                target["code"] = code
                return points, {1}
            return points, set()

        evaluation, summary, _ = self._assess("warehouse_staff", plan)
        self.assertTrue(target)
        self.assertEqual(summary.status, SessionEvaluationSummary.STATUS_EVALUATION_FAILED)
        self.assertEqual([f["question_code"] for f in summary.critical_failures], [target["code"]])
        self.assertEqual(evaluation.readiness_status, ReadinessStatus.NOT_READY)
        self.assertEqual(evaluation.review_status, Evaluation.REVIEW_NOT_REQUIRED)
        failed = evaluation.response_results.get(critical_failure=True)
        self.assertEqual(failed.score, Decimal("0.00"))
        self.assertFalse(failed.requires_human_review)
        self.assertFalse(certificate_eligibility(evaluation, summary)[0])

    def test_competency_pass_mark_is_60_percent_whatever_the_draw(self):
        evaluation, summary, _ = self._assess("hotel_housekeeper", all_points)
        thresholds = set(evaluation.competency_results.values_list("pass_threshold", flat=True))
        self.assertEqual(thresholds, {Decimal("60.00")})
        self.assertEqual(evaluation.readiness_status, ReadinessStatus.READY)
        self.assertTrue(certificate_eligibility(evaluation, summary)[0])

    def test_role_profile_exception_overrides_the_default_pass_mark(self):
        bank = QuestionBankVersion.objects.get(version=DRAFT)
        bank.role_profiles = [
            {**p, "competency_thresholds": {"safety_awareness": 80}} if p["role_code"] == "driver" else p
            for p in bank.role_profiles
        ]
        bank.save(update_fields=["role_profiles"])
        evaluation, _, _ = self._assess("driver", all_points)
        safety = evaluation.competency_results.get(competency_code="safety_awareness")
        self.assertEqual(safety.pass_threshold, Decimal("80.00"))
        others = evaluation.competency_results.exclude(competency_code="safety_awareness")
        self.assertEqual(set(others.values_list("pass_threshold", flat=True)), {Decimal("60.00")})

    def test_competency_at_exactly_60_percent_meets_the_mark(self):
        # One generic question at 3/5 and nothing else in that competency
        # would be 60.00%: below-threshold status must not apply at the mark.
        evaluation, _, _ = self._assess("hotel_housekeeper", all_points)
        result = evaluation.competency_results.first()
        result.total_score, result.max_score = Decimal("3"), Decimal("5")
        self.assertEqual(Week6ScoringService._percentage(result.total_score, result.max_score), Decimal("60.00"))
        self.assertFalse(Decimal("60.00") < result.pass_threshold)
        self.assertEqual(result.status, CompetencyEvaluationResult.STATUS_MEETS_THRESHOLD)
