"""GOV2.0-RC1 question bank: package integrity, competency-allocation
selection, inert draft import, the four transition changes (version-aware
rubrics, scoring rules and question generation; activation/rollback), and
the bilingual end-to-end assessment journey.

The LLM is the only thing replaced (by the controlled extractor used in the
launch-safeguard suite); everything else runs through the production code."""
import json
import random
from collections import Counter
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.audit.models import AuditLog
from api.candidates.models import Candidate
from api.contracts.models import Agreement
from api.core.constants import (
    AgreementMethod, AgreementType, EvaluationType, InterviewEvaluationTier, QuestionLifecycleStatus,
    ReadinessStatus, Roles,
)
from api.dashboard.comparison_services import _current_rule_set_ids
from api.evaluations.certificate_services import certificate_eligibility
from api.evaluations.models import Evaluation, ScoringRuleSet
from api.evaluations.scoring_services import Week6ScoringService
from api.evaluations.tests_launch_safeguards import Extractor
from api.interviews.management.commands.import_question_bank_draft import Command as DraftImport, split_points
from api.interviews.models import InterviewConfiguration, InterviewRubric, QuestionBankVersion
from api.interviews.question_allocation import (
    FULL, SCREENING, AllocationGap, QuestionCandidate, critical_competencies, select_questions,
)
from api.questions.models import IndicatorDefinition, QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession
from api.sessions.services import QuestionGenerationService
from api.translation.services import AIProcessingOrchestrationService, ResponseInterpretationService

BANK = Path(__file__).resolve().parent / "fixtures" / "question_bank"
V12 = json.loads((BANK / "governance_v1_2_corrected.json").read_text(encoding="utf-8"))
V2 = json.loads((BANK / "governance_v2_0.json").read_text(encoding="utf-8"))
CORRECTIONS = Path(__file__).resolve().parents[2] / "scripts" / "question_bank" / "corrections"
LIVE = "GOV1.2-FINAL"
DRAFT = "GOV2.0-RC1"
V2_BY_CODE = {q["question_code"]: q for q in V2["questions"]}
V12_BY_CODE = {q["question_code"]: q for q in V12["questions"]}
PROMPT_VERBS = {"Describe", "Demonstrate", "Name", "Explain", "Show", "List", "Walk", "Tell", "Give", "Outline", "Identify", "State"}


class PackageIntegrityTests(SimpleTestCase):
    def test_package_is_a_release_candidate_with_2100_unique_questions_100_per_role(self):
        self.assertEqual(V2["version"], DRAFT)
        self.assertTrue(V2["status"].startswith("RELEASE CANDIDATE"))
        self.assertEqual(len(V2_BY_CODE), 2100)
        self.assertEqual(set(Counter(q["role"] for q in V2["questions"]).values()), {100})
        self.assertEqual(V2["scoring"]["aggregation"], "RESPONSE_SUM")

    def test_every_approved_question_keeps_its_sets_competency_and_scoring(self):
        for live in V12["questions"]:
            q = V2_BY_CODE[live["question_code"]]
            self.assertEqual(q["v1_2_full_set"], live["full_flag"] == "Yes")
            self.assertEqual(q["v1_2_screening_set"], live["is_screening"])
            self.assertEqual(q["score_note"], live["score_note"])
            self.assertEqual(q["competency_code"], live["competency_code"])

    def test_d06_questions_are_identical_to_the_approved_version(self):
        for code in ("CCG-TSK-003", "IC-TSK-003", "SNC-GEN-009"):
            q, live = V2_BY_CODE[code], V12_BY_CODE[code]
            for field in ("question_en", "question_ar", "must_include_en", "must_include_ar",
                          "ideal_keywords", "negative_indicators"):
                self.assertEqual(q[field], live[field], (code, field))
            self.assertTrue(q["proposed_for_sme"])

    def test_approved_criticality_changes_are_applied_and_recorded(self):
        changed = {c: q for c, q in V2_BY_CODE.items() if "criticality_change" in q}
        self.assertEqual(set(changed), {"SNC-GEN-005", "CCG-PRO-004", "ECG-PRO-004", "RS-BAD-003"})
        for code, q in changed.items():
            self.assertEqual(V12_BY_CODE[code]["criticality"], "Non-Critical")
            self.assertEqual(q["criticality"], "Critical")
            self.assertEqual(q["criticality_change"]["approved"], "2026-10-13")
        # Skilled Trades Communication stays Non-Critical, safeguarded by
        # question-specific S3 negatives on the safety-handover questions.
        self.assertEqual(_profile("Skilled Trades")["criticality"]["communication_ability"], "Non-Critical")
        for code in ("ST-COM-003", "ST-COM-008", "ST-COM-016", "ST-COM-019"):
            self.assertIn("S3", V2_BY_CODE[code]["negative_severity"], code)

    def test_duplicates_replaced_with_same_identity(self):
        for code, comp in (("FW-BEH-004", "behavior_integrity"), ("RS-TSK-008", "task_execution")):
            q = V2_BY_CODE[code]
            self.assertEqual(q["competency_code"], comp)
            self.assertIn("DUPLICATE_REPLACEMENT", q["corrections_applied"])
            self.assertEqual(q["review_status"], "AI_DRAFT_PENDING_SME")
        self.assertNotEqual(V2_BY_CODE["FW-BEH-004"]["question_en"], V2_BY_CODE["FW-BEH-002"]["question_en"])

    def test_no_question_is_a_bare_scenario(self):
        for q in V2["questions"]:
            text = q["question_en"].rstrip()
            self.assertTrue(text.endswith("?") or text.split()[0] in PROMPT_VERBS, q["question_code"])

    def test_negative_indicators_are_question_specific_with_d04_severity(self):
        counts = Counter(q["negative_indicators"] for q in V2["questions"])
        self.assertFalse([t for t, n in counts.items() if n >= 5])
        for q in V2["questions"]:
            if "negative_severity" in q:
                self.assertEqual(len(q["negative_severity"]), len(split_points(q["negative_indicators"])), q["question_code"])
                self.assertTrue(set(q["negative_severity"]) <= {"S1", "S2", "S3"})

    def test_mandatory_points_are_a_subset_of_must_include(self):
        classified = [q for q in V2["questions"] if q.get("mandatory_indicators")]
        self.assertEqual(len(classified), 89)
        for q in classified:
            self.assertLessEqual(set(q["mandatory_indicators"]), set(split_points(q["must_include_en"])))

    def test_new_questions_use_5_3_0_and_approved_exceptions_are_preserved(self):
        from api.interviews.management.commands.import_governance_question_bank import _scoring_shape

        shapes = Counter((q["source"], _scoring_shape(q["score_note"])["scoring_type"]) for q in V2["questions"])
        self.assertEqual({t for (s, t) in shapes if s == "expansion"}, {"0/3/5"})
        self.assertEqual(sum(n for (s, t), n in shapes.items() if s == "approved_v1_2" and t != "0/3/5"), 70)

    def test_matrix_is_the_role_specific_proposal_and_meets_the_rules(self):
        proposal = json.loads((CORRECTIONS / "07_allocation_matrix.json").read_text(encoding="utf-8"))["roles"]
        pool = Counter((q["role"], q["competency_code"]) for q in V2["questions"])
        for profile in V2["role_profiles"]:
            role, critical = profile["role"], critical_competencies(profile)
            self.assertEqual(profile["allocation"]["FULL"], proposal[role]["FULL"])
            self.assertEqual(sum(profile["allocation"][FULL].values()), 21)
            self.assertEqual(sum(profile["allocation"][SCREENING].values()), 7)
            for comp in critical:
                self.assertGreaterEqual(profile["allocation"][FULL].get(comp, 0), 3, (role, comp))
                self.assertGreaterEqual(profile["allocation"][SCREENING].get(comp, 0), 1, (role, comp))
            for comp, n in profile["allocation"][FULL].items():
                self.assertLessEqual(2 * n, pool[(role, comp)], (role, comp))  # room for fresh retakes

    def test_package_passes_importer_validation_and_refuses_non_drafts(self):
        DraftImport.validate(V2)
        with self.assertRaises(CommandError):
            DraftImport.validate({**V2, "status": "FINAL"})
        with self.assertRaises(CommandError):
            DraftImport.validate({**V2, "version": "GOV2.0-FINAL"})
        with self.assertRaises(CommandError):
            DraftImport.validate({**V2, "version": LIVE})

    def test_arabic_semicolon_is_split(self):
        self.assertEqual(split_points("أ؛ ب؛ ج", "AR"), ["أ", "ب", "ج"])


class PerQuestionExtractor(Extractor):
    """The controlled extractor, answering each question by its own mode:
    `mode_for(question_code)` returns complete / none / unsafe_with_steps."""

    def __init__(self, mode_for):
        super().__init__("complete")
        self.mode_for = mode_for

    def interpret(self, *, prompt):
        ids = [i["id"] for i in json.loads(prompt)["question"].get("indicators") or []]
        code = ids[0].removeprefix("V2-").rsplit("-", 2)[0] if ids else ""
        self.mode, self.calls = self.mode_for(code), 0
        return super().interpret(prompt=prompt)


def _pool(role, language="EN"):
    return [QuestionCandidate.from_fixture(q, language) for q in V2["questions"] if q["role"] == role]


def _profile(role):
    return next(p for p in V2["role_profiles"] if p["role"] == role)


class AllocationSelectionTests(SimpleTestCase):
    def _select(self, role, role_code, tier, **kwargs):
        profile = _profile(role)
        params = dict(pool=_pool(role), allocation=profile["allocation"][tier], critical=critical_competencies(profile),
                      tier=tier, role_code=role_code, language="EN", rng=random.Random(1))
        params.update(kwargs)
        return select_questions(**params)

    def test_exact_allocation_screening_eligible_only_and_skill_matching_secondary(self):
        for tier, n in ((FULL, 21), (SCREENING, 7)):
            result = self._select("Nursing Assistant", "nursing_assistant", tier,
                                  candidate_skills=["medication reporting", "hand hygiene"])
            self.assertEqual(len(result.questions), n)
            self.assertEqual(Counter(q.competency_code for q in result.questions),
                             Counter(_profile("Nursing Assistant")["allocation"][tier]))
        self.assertTrue(all(q.screening_eligible for q in self._select("Driver", "driver", SCREENING).questions))

    def test_wrong_language_or_role_and_short_pools_raise_gaps(self):
        with self.assertRaises(AllocationGap):
            self._select("Driver", "driver", FULL, language="AR")
        with self.assertRaises(AllocationGap):
            self._select("Driver", "nursing_assistant", FULL)
        pool = [q for q in _pool("Driver") if q.competency_code != "task_execution"]
        with self.assertRaises(AllocationGap):
            self._select("Driver", "driver", FULL, pool=pool)

    def test_every_role_gets_a_fully_fresh_full_retake(self):
        role_map = V2["role_code_map"]
        for role, role_code in role_map.items():
            first = self._select(role, role_code, FULL)
            retake = self._select(role, role_code, FULL, previous_codes=[q.code for q in first.questions],
                                  rng=random.Random(2))
            self.assertEqual(retake.reused_codes, [], role)


class TransitionAndBilingualTests(TestCase):
    """GOV1.2 live, GOV2.0-RC1 imported, then activated and rolled back -
    the cutover path - with assessments started on each side of it."""

    @classmethod
    def setUpTestData(cls):
        call_command("import_governance_question_bank", verbosity=0)
        cls.v12_rubric_ids = set(InterviewRubric.objects.values_list("id", flat=True))
        cls.v12_active_rule_sets = set(ScoringRuleSet.objects.filter(is_active=True).values_list("id", flat=True))
        cls.configs_before = list(InterviewConfiguration.objects.order_by("id").values())
        call_command("import_question_bank_draft", verbosity=0)
        cls.owner = User.objects.create_user(email="v2@example.com", password="x", first_name="V", last_name="Two",
                                             role=Roles.B2B, is_verified=True)
        cls.company = Company.objects.create(name="V2 Co", registration_number="V2-1", company_size="11-50",
                                             phone_number="+1", country="X", city="Y", admin_user=cls.owner)
        CompanyEmployerProfile.objects.create(user=cls.owner, company_name=cls.company.name, company=cls.company,
                                              company_registration_number="V2-1", company_size="11-50")

    def setUp(self):
        self.seq = 0

    # -- helpers ------------------------------------------------------------

    def _candidate(self, lang="EN"):
        self.seq += 1
        return Candidate.objects.create(first_name="C", last_name=str(self.seq), email=f"v2-{self.seq}@example.com",
                                        passport_id=f"V2{self.seq}", job_role="OT", core_skills="",
                                        preferred_language=lang, passport_document="candidates/documents/passport/t.pdf",
                                        created_by=self.owner, company=self.company)

    def _session(self, role_code, lang="EN", tier=InterviewEvaluationTier.FULL, candidate=None, version=None):
        config = InterviewConfiguration.objects.filter(role_code=role_code, language=lang,
                                                       evaluation_tier=tier).order_by("-id").first()
        version = version or config.question_set_version
        consent = Agreement.objects.create(user=self.owner, agreement_type=AgreementType.CANDIDATE_CONSENT, version="1",
                                           method=AgreementMethod.CHECKBOX, status="SIGNED", accepted_at=timezone.now())
        full = tier == InterviewEvaluationTier.FULL
        return InterviewSession.objects.create(
            candidate=candidate or self._candidate(lang), organization=self.company, config=config,
            role_name=config.role_name, role_code=role_code, ui_language="EN", candidate_language=lang,
            tts_language_code="en-US", stt_language_code="en-US" if lang == "EN" else "ar-SA", evaluation_tier=tier,
            readiness_indicator_enabled=full, certificate_enabled=full, rubric_version=version,
            question_set_version=version, identity_verified=True, candidate_consent_agreement=consent,
            status="COMPLETED", ended_at=timezone.now(), expires_at=InterviewSession.build_expiry(30),
            created_by=self.owner)

    def _assess(self, session, mode="complete"):
        questions = QuestionGenerationService.generate_questions(session)
        evaluation, summary = self._score(session, questions, mode)
        return evaluation, summary, questions

    def _score(self, session, questions, mode):
        full = session.evaluation_tier == InterviewEvaluationTier.FULL
        evaluation = Evaluation.objects.create(
            session=session, candidate=session.candidate, evaluation_type=EvaluationType.INTERVIEW, status="COMPLETED",
            scheduled_date=timezone.now(), duration_minutes=45, created_by=self.owner, company=self.company,
            evaluation_tier=session.evaluation_tier, readiness_indicator_enabled=full, certificate_enabled=full)
        extractor = Extractor(mode) if isinstance(mode, str) else mode
        with mock.patch.object(ResponseInterpretationService, "get_provider", return_value=extractor):
            for q in questions:
                r = CandidateResponse.objects.create(session=session, question=q, transcript="(answer)",
                                                     original_transcript="(answer)",
                                                     transcript_language=session.candidate_language.lower())
                AIProcessingOrchestrationService.interpret_response(response=r)
                r.refresh_from_db()
                AIProcessingOrchestrationService.prepare_evaluation_input(response=r)
        summary = Week6ScoringService.run_for_evaluation(evaluation=evaluation)
        evaluation.refresh_from_db()
        return evaluation, summary

    def _activate(self):
        call_command("activate_question_bank_version", DRAFT, verbosity=0)

    # -- draft import is inert ----------------------------------------------

    def test_draft_import_is_inert(self):
        draft = QuestionTemplate.objects.filter(question_set_version=DRAFT)
        self.assertEqual(draft.count(), 4200)
        self.assertFalse(draft.filter(is_active=True).exists())
        self.assertFalse(ScoringRuleSet.objects.filter(question_set_version=DRAFT, is_active=True).exists())
        self.assertFalse(InterviewRubric.objects.filter(rubric_version=DRAFT, is_active=True).exists())
        self.assertEqual(set(ScoringRuleSet.objects.filter(is_active=True).values_list("id", flat=True)),
                         self.v12_active_rule_sets)
        self.assertEqual(list(InterviewConfiguration.objects.order_by("id").values()), self.configs_before)
        self.assertEqual(IndicatorDefinition.objects.filter(bank_version=DRAFT).exclude(text_ar="").count(), 0)
        live = self._session("driver")
        questions = QuestionGenerationService.generate_questions(live)
        self.assertEqual({q.question_template.question_set_version for q in questions}, {LIVE})
        self.assertEqual(QuestionBankVersion.objects.get(version=DRAFT).status, QuestionBankVersion.STATUS_DRAFT)

    # -- transition 1-3: each assessment uses its own versions ---------------

    def test_after_activation_new_assessments_use_v2_and_in_flight_v12_ones_keep_v12(self):
        in_flight = self._session("nursing_assistant", "AR")  # created on GOV1.2 before the switch
        self._activate()
        for tier, n in ((InterviewEvaluationTier.FULL, 21), (InterviewEvaluationTier.SCREENING, 7)):
            for lang in ("EN", "AR"):
                with self.subTest(tier=tier, lang=lang):
                    session = self._session("nursing_assistant", lang, tier)
                    self.assertEqual(session.question_set_version, DRAFT)
                    questions = QuestionGenerationService.generate_questions(session)
                    templates = [q.question_template for q in questions]
                    self.assertEqual(len(templates), n)
                    self.assertEqual({t.question_set_version for t in templates}, {DRAFT})
                    self.assertEqual({t.role_code for t in templates}, {"nursing_assistant"})
                    self.assertEqual({t.language for t in templates}, {lang})
                    key = FULL if tier == InterviewEvaluationTier.FULL else SCREENING
                    self.assertEqual(Counter(t.skill_id for t in templates),
                                     Counter(_profile("Nursing Assistant")["allocation"][key]))
        old = QuestionGenerationService.generate_questions(in_flight)
        self.assertEqual({q.question_template.question_set_version for q in old}, {LIVE})

    def test_scoring_and_rubrics_resolve_to_each_sessions_own_version(self):
        v12_session = self._session("driver", "EN")
        self._activate()
        v12_eval, v12_summary, _ = self._assess(v12_session)
        v2_eval, v2_summary, v2_questions = self._assess(self._session("driver", "EN"))
        self.assertEqual(v12_eval.scoring_rule_set.question_set_version, "")
        self.assertEqual(v12_eval.scoring_rule_set.version, "governance-v1.2-corrected")
        self.assertEqual(v2_eval.scoring_rule_set.question_set_version, DRAFT)
        self.assertEqual(v2_eval.scoring_rule_set.aggregation_method, V2["scoring"]["aggregation"])
        response = CandidateResponse.objects.filter(question=v2_questions[0]).first()
        prompt = json.loads(ResponseInterpretationService.build_prompt(
            response=response, transcript="x", input_language="en", input_transcript_type="TRANSCRIPT"))
        refs = {c["question_ref"] for c in prompt["question"]["rubric_notes"]}
        self.assertIn(v2_questions[0].question_template.question_code, refs)
        self.assertEqual(InterviewRubric.objects.filter(rubric_version=DRAFT, is_active=True).count() > 0, True)

    def test_overall_score_uses_the_packages_weighting_method(self):
        self._activate()
        fail_safety = PerQuestionExtractor(
            lambda code: "none" if V2_BY_CODE[code]["competency_code"] == "safety_awareness" else "complete")
        evaluation, summary, _ = self._assess(self._session("driver", "EN"), mode=fail_safety)
        # Points-based: Safety's 8 of 21 approved questions weigh 8/21.
        self.assertEqual(summary.overall_percentage, (summary.total_score / summary.max_score * 100).quantize(Decimal("0.01")))
        # The equal-competency method (compared, not chosen) still works.
        rule_set = evaluation.scoring_rule_set
        rule_set.aggregation_method = ScoringRuleSet.AGGREGATION_EQUAL_COMPETENCY
        rule_set.save(update_fields=["aggregation_method"])
        summary = Week6ScoringService.run_for_evaluation(evaluation=evaluation)
        results = [r for r in evaluation.competency_results.all() if r.completed_response_count]
        expected = sum(r.percentage for r in results) / len(results)
        self.assertEqual(summary.overall_percentage, Decimal(expected).quantize(Decimal("0.01")))

    def test_retake_uses_unseen_alternative_questions(self):
        self._activate()
        candidate = self._candidate()
        first = QuestionGenerationService.generate_questions(self._session("driver", candidate=candidate))
        retake = QuestionGenerationService.generate_questions(self._session("driver", candidate=candidate))
        first_codes = {q.question_template.question_code for q in first}
        self.assertFalse(first_codes & {q.question_template.question_code for q in retake})

    def test_pool_gap_is_flagged_and_no_questions_are_created(self):
        self._activate()
        QuestionTemplate.objects.filter(question_set_version=DRAFT, role_code="driver",
                                        skill_id="task_execution").update(is_active=False)
        session = self._session("driver")
        with self.assertRaises(AllocationGap):
            QuestionGenerationService.generate_questions(session)
        self.assertFalse(session.questions.exists())
        log = AuditLog.objects.filter(description__icontains="selection gap").latest("created_at")
        self.assertIn("task_execution", json.dumps(log.data))

    # -- transition 4: activation, rollback, historical integrity -----------

    def test_rollback_restores_configuration_and_preserves_history(self):
        historical, historical_summary, _ = self._assess(self._session("child_caregiver", "AR"))
        before = (historical_summary.overall_percentage, historical.readiness_status, historical.scoring_rule_set_id)
        self._activate()
        live_v2 = self._session("child_caregiver", "EN")
        QuestionGenerationService.generate_questions(live_v2)
        call_command("activate_question_bank_version", DRAFT, "--rollback", verbosity=0)
        self.assertEqual(list(InterviewConfiguration.objects.order_by("id").values("id", "question_set_version",
                         "rubric_version", "total_questions", "is_active")),
                         [{k: c[k] for k in ("id", "question_set_version", "rubric_version", "total_questions", "is_active")}
                          for c in self.configs_before])
        self.assertFalse(QuestionTemplate.objects.filter(question_set_version=DRAFT, is_active=True).exists())
        # The assessment started while GOV2.0 was live still scores on GOV2.0.
        v2_eval, _, _ = self._assess(live_v2)
        self.assertEqual(v2_eval.scoring_rule_set.question_set_version, DRAFT)
        # History is untouched, including a re-score (pinned rule set).
        historical.refresh_from_db()
        Week6ScoringService.run_for_evaluation(evaluation=historical)
        historical.refresh_from_db()
        summary = historical.session_summaries.order_by("-created_at").first()
        self.assertEqual((summary.overall_percentage, historical.readiness_status, historical.scoring_rule_set_id), before)
        # New assessments are back on GOV1.2.
        after = QuestionGenerationService.generate_questions(self._session("child_caregiver", "EN"))
        self.assertEqual({q.question_template.question_set_version for q in after}, {LIVE})

    def test_activation_is_refused_twice_and_rollback_needs_an_active_version(self):
        with self.assertRaises(CommandError):
            call_command("activate_question_bank_version", DRAFT, "--rollback", verbosity=0)
        self._activate()
        with self.assertRaises(CommandError):
            self._activate()
        self.assertEqual(QuestionBankVersion.objects.get(version=DRAFT).status, QuestionBankVersion.STATUS_ACTIVE)

    def test_comparison_current_rule_sets_follow_activation(self):
        before = _current_rule_set_ids("COMPANY", self.company, "driver")
        self.assertEqual(set(ScoringRuleSet.objects.filter(id__in=before).values_list("question_set_version", flat=True)), {""})

    # -- bilingual end-to-end journey ----------------------------------------

    def _journey(self, role_code, lang, tier, mode):
        evaluation, summary, questions = self._assess(self._session(role_code, lang, tier), mode=mode)
        competencies = {r.competency_code: r.percentage for r in evaluation.competency_results.all()}
        return {
            "questions": len(questions),
            "overall": summary.overall_percentage,
            "readiness": evaluation.readiness_status,
            "review": evaluation.review_status,
            "competencies": competencies,
            "certificate": certificate_eligibility(evaluation, summary),
        }, evaluation

    def test_bilingual_journey_gives_identical_outcomes(self):
        self._activate()
        roles = ("driver", "nursing_assistant", "child_caregiver", "hotel_housekeeper", "skilled_trades")
        for role_code in roles:
            for tier in (InterviewEvaluationTier.FULL, InterviewEvaluationTier.SCREENING):
                for mode in ("complete", "none", "unsafe_with_steps"):
                    with self.subTest(role=role_code, tier=tier, mode=mode):
                        en, _ = self._journey(role_code, "EN", tier, mode)
                        ar, _ = self._journey(role_code, "AR", tier, mode)
                        self.assertEqual(en, ar)
                        self.assertEqual(en["questions"], 21 if tier == InterviewEvaluationTier.FULL else 7)
                        if mode == "complete":
                            self.assertEqual(en["overall"], Decimal("100.00"))
                            if tier == InterviewEvaluationTier.FULL:
                                self.assertEqual(en["readiness"], ReadinessStatus.READY)
                                self.assertTrue(en["certificate"][0], en["certificate"])
                            else:
                                self.assertEqual(en["certificate"], (False, "SCREENING_NO_CERTIFICATE"))
                        if mode == "none":
                            self.assertEqual(en["overall"], Decimal("0.00"))
                            self.assertNotEqual(en["readiness"], ReadinessStatus.READY)
                        if mode == "unsafe_with_steps":
                            self.assertEqual(en["review"], Evaluation.REVIEW_REQUIRED)
                            self.assertNotEqual(en["readiness"], ReadinessStatus.READY)

    # -- approved methodology gates (management decisions, 13 Oct 2026) -----

    def test_readiness_follows_the_approved_role_profile(self):
        self._activate()

        def fail(competency):
            return PerQuestionExtractor(
                lambda code: "none" if V2_BY_CODE[code]["competency_code"] == competency else "complete")

        cases = (
            # Skilled Trades Communication is Non-Critical: failing it alone
            # is Partially Ready, never Not Ready.
            ("skilled_trades", "communication_ability", ReadinessStatus.PARTIALLY_READY),
            # Elderly Caregiver Communication is Critical: Not Ready.
            ("elderly_caregiver", "communication_ability", ReadinessStatus.NOT_READY),
            ("driver", "safety_awareness", ReadinessStatus.NOT_READY),
        )
        for role_code, competency, expected in cases:
            for lang in ("EN", "AR"):
                with self.subTest(role=role_code, lang=lang):
                    evaluation, summary, _ = self._assess(self._session(role_code, lang), mode=fail(competency))
                    self.assertEqual(evaluation.readiness_status, expected, evaluation.readiness_override_reason)
                    self.assertFalse(certificate_eligibility(evaluation, summary)[0])

    def test_approved_critical_questions_gate_release_without_automatic_not_ready(self):
        self._activate()
        for code in ("SNC-GEN-005", "CCG-PRO-004", "ECG-PRO-004", "RS-BAD-003"):
            self.assertEqual(QuestionTemplate.objects.filter(question_set_version=DRAFT, question_code=code,
                                                             critical_question=True).count(), 2, code)
        for role_code, code in (("child_caregiver", "CCG-PRO-004"), ("restaurant_staff", "RS-BAD-003")):
            for lang in ("EN", "AR"):
                with self.subTest(code=code, lang=lang):
                    session = self._session(role_code, lang)
                    questions = QuestionGenerationService.generate_questions(session)
                    # Make sure the approved Critical question is in this
                    # assessment: swap it in for a same-competency question.
                    target = QuestionTemplate.objects.get(question_set_version=DRAFT, question_code=code, language=lang)
                    if target.id not in {q.question_template_id for q in questions}:
                        slot = next(q for q in questions if q.question_template.skill_id == target.skill_id)
                        slot.question_template = target
                        slot.question_text = target.question_text
                        slot.save(update_fields=["question_template", "question_text"])
                    evaluation, summary = self._score(
                        session, list(session.questions.all()),
                        PerQuestionExtractor(lambda c, code=code: "none" if c == code else "complete"))
                    # The other 20 answers carry the result...
                    self.assertGreaterEqual(summary.overall_percentage, Decimal("80"))
                    self.assertIn(evaluation.readiness_status, (ReadinessStatus.READY, ReadinessStatus.PARTIALLY_READY))
                    # ...but it is held, not released and not certified.
                    self.assertEqual(evaluation.review_status, Evaluation.REVIEW_REQUIRED)
                    self.assertTrue(any(code in r for r in evaluation.review_reasons), evaluation.review_reasons)
                    self.assertEqual(certificate_eligibility(evaluation, summary), (False, "HUMAN_REVIEW_PENDING"))

    def test_safety_failure_cannot_be_overridden_by_the_overall_score(self):
        self._activate()
        for lang in ("EN", "AR"):
            with self.subTest(lang=lang):
                # Every required point present, plus one affirmed unsafe act.
                evaluation, summary, _ = self._assess(self._session("skilled_trades", lang), mode="unsafe_with_steps")
                self.assertEqual(evaluation.review_status, Evaluation.REVIEW_REQUIRED)
                self.assertNotEqual(evaluation.readiness_status, ReadinessStatus.READY)
                self.assertEqual(certificate_eligibility(evaluation, summary)[0], False)

    def test_reports_are_generated_for_both_languages(self):
        from api.reports.services import EvaluationReportService

        self._activate()
        for lang in ("EN", "AR"):
            with self.subTest(lang=lang):
                _, evaluation = self._journey("restaurant_staff", lang, InterviewEvaluationTier.FULL, "complete")
                report = EvaluationReportService.generate_for_evaluation(evaluation=evaluation, actor=self.owner)
                self.assertIsNotNone(report)
