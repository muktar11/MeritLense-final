"""Management decisions of 20 Oct 2026 on AI-path scoring (docs/ASSESSMENT_RULES.md):
1. each question scored by its own approved rubric, including partial credit
   and the 70 legacy exceptions;
2. no Must Include point mandatory unless an approved specification says so
   (none of the 89 D-02 drafts applies); an affirmed unsafe action is scored
   by the rules, not routed to review;
5. one fixed competency pass mark (60% unless the role profile carries an
   approved exception), whichever questions are drawn.

Management decisions of 22 Oct 2026: approved rule templates with
question-specific mappings (5 / 3 / 0); partial credit kept for the 24
all-central questions where it can be defined, binary otherwise with a
justification; only questions with complete rules are selected, else an
availability exception; conditional points count only when established;
the additional Core proposals adopted where supported; ECG-TSK-001 on its
four original outcomes with the safeguard consequence escalated.

The LLM is replaced by a controlled extractor; everything else runs through
the production code on the GOV2.0-RC1 bank."""
import json
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
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


def without_templates():
    """The configuration as it was before the templates were approved
    (status PROPOSED), without changing the fixture: generic-note questions
    fall back to the older generic band."""
    spec_set = rubric_specs.spec_set_for_version(DRAFT)
    classification = json.loads(json.dumps(spec_set["classification"]))
    for template in classification["templates"].values():
        template["status"] = "PROPOSED"
    proposed = {**spec_set, "classification": classification}
    return mock.patch.object(rubric_specs, "spec_set_for_version",
                             lambda version: proposed if version == DRAFT else None)


def run(code, matched, negatives=(), total=None, established=()):
    _, spec = rubric_specs.spec_for(DRAFT, code)
    q = V2_BY_CODE[code]
    total = total if total is not None else len(split_points(q["must_include_en"]))
    from api.interviews.management.commands.import_governance_question_bank import _scoring_shape

    return rubric_specs.execute(spec, max_score=_scoring_shape(q["score_note"])["max_score"],
                                matched_ordinals=matched, total_points=total, negative_ordinals=negatives,
                                established_ordinals=established)


class RubricExecutionTests(SimpleTestCase):
    def test_every_question_in_the_bank_has_an_executable_rubric(self):
        for code in V2_BY_CODE:
            version, spec = rubric_specs.spec_for(DRAFT, code)
            self.assertTrue(version.startswith(SPECS["spec_version"]), code)
            self.assertIn(spec["kind"], ("generic_530", "bands", "completion"), code)
        self.assertEqual(len(SPECS["questions"]), 74)  # 70 legacy + 4 question-specific 5/3/0

    def test_generic_note_full_and_one_missing_point_without_a_rule(self):
        code = "HK-TSK-002"  # generic note, 7 points, carries a D-02 mandatory draft
        with without_templates():
            self.assertEqual(run(code, range(1, 8))["score"], Decimal("5.00"))
            one_missing = run(code, [1, 2, 3, 4, 5, 6])
            self.assertEqual((one_missing["score"], one_missing["band"]), (Decimal("3.00"), rubric_specs.BAND_PARTIAL))

    def test_d02_mandatory_draft_does_not_zero_an_answer(self):
        # "Use gloves/PPE as required" is point 1 of HK-TSK-002 and one of its
        # 89 unapproved D-02 drafts: missing it alone never zeroes an answer.
        with without_templates():
            result = run("HK-TSK-002", [2, 3, 4, 5, 6, 7])
            self.assertEqual(result["score"], Decimal("3.00"))
            self.assertFalse(result["critical_failure"])
        # Under its rule it is a conditional point not established: 5.
        self.assertEqual(run("HK-TSK-002", [2, 3, 4, 5, 6, 7])["score"], Decimal("5.00"))

    def test_two_or_more_missing_without_a_rule_is_pending_not_zero(self):
        # Decision A (20 Oct 2026): no interim zero for customer scoring.
        with without_templates():
            result = run("HK-TSK-002", [1, 2, 3, 4, 5])
            self.assertEqual(result["band"], rubric_specs.BAND_MULTI_OMISSION)
            self.assertEqual(result["status"], rubric_specs.STATUS_PENDING)
            self.assertIsNone(result["score"])
            # A hard negative is still a defined 0, whatever is missing.
            self.assertEqual(run("HK-TSK-002", [1], negatives=[2])["score"], Decimal("0.00"))
            # No required point at all is insufficient evidence (SCR-03): 0.
            nothing = run("HK-TSK-002", [])
            self.assertEqual((nothing["score"], nothing["status"]), (Decimal("0.00"), "DEFINED"))
        # With the approved template there is no held band.
        self.assertEqual(run("HK-TSK-002", [1, 2, 3, 4, 5])["status"], "DEFINED")

    def test_reviewed_classification_with_adopted_additions(self):
        # Management review of 20 Oct 2026 is the classification source; the
        # 22 Oct decisions adopt the supported additional Core proposals.
        classification = json.loads((BANK / "rubric_core_points_gov2_0.json").read_text(encoding="utf-8"))
        self.assertEqual(classification["classification_version"], "GOV2.0-RC1-core-reviewed-2026-10-22")
        self.assertEqual(len(classification["questions"]), 2026)
        # No question-specific rule needs approval: every question is scored
        # by its approved template.
        self.assertEqual({q["review_status"] for q in classification["questions"].values()}, {"NOT_APPROVED"})
        additions = [a for q in classification["questions"].values() for a in q.get("core_additions", [])]
        self.assertEqual(len(additions), 147)
        adopted = sum(a["decision"] == "ADOPTED" for a in additions)
        self.assertEqual(adopted, 80)
        self.assertTrue(all(a["rationale"] and a["basis"] for a in additions))
        self.assertEqual(sum(len(q["core"]) for q in classification["questions"].values()), 6247 + adopted)
        self.assertEqual(sum(len(q["revised_points"]) for q in classification["questions"].values()), 539)
        for code, entry in classification["questions"].items():
            total = len(split_points(V2_BY_CODE[code]["must_include_en"]))
            self.assertTrue(set(entry["core"]) <= set(range(1, total + 1)), code)
            self.assertNotIn("rule", entry)  # no count-based or inferred rule
            for addition in entry.get("core_additions", []):
                # Classification alone creates no gate: an adopted point is
                # never central, a prerequisite or a negative cap.
                self.assertNotIn(addition["point"], entry["mapping"]["central"], code)
                self.assertEqual(addition["point"] in entry["core"], addition["decision"] == "ADOPTED", code)

    # HK-TSK-002 (7 points): 1 gloves/PPE as required, 2 remove debris,
    # 3 surface-appropriate cleaner, 4 clean then disinfect, 5 avoid
    # cross-contamination, 6 rinse/dry if required, 7 final check and hand
    # hygiene. An illustrative rule (not an approved one) exercising every
    # predicate: point 4 is a prerequisite, point 1 is conditional.
    ILLUSTRATIVE_RULE = {
        "rule_version": "TEST-1",
        "full": [[1], [4], [5], [7]],
        "partial": [[4], [5]],
        "prerequisites": [4],
        "conditional": [1],
        "negative_caps": {},
    }

    def _cases(self, rule):
        def case(category, matched, expected, negatives=(), inapplicable=(), established=(1,)):
            return {"category": category, "matched": list(matched), "negatives": list(negatives),
                    "inapplicable": list(inapplicable), "established": list(established), "total_points": 7,
                    "expected": expected}

        return [
            case("full", [1, 2, 3, 4, 5, 6, 7], {"score": 5}),
            case("partial", [1, 2, 4, 5], {"score": 3}),
            case("supporting_omission", [1, 4, 5, 7], {"score": 5}),
            case("below_minimum", [1, 2, 3, 4, 6, 7], {"score": 0}),
            case("prerequisite_omission", [1, 2, 3, 5, 6, 7], {"score": 0}),
            case("condition_inapplicable", [4, 5, 7], {"score": 5}, inapplicable=[1]),
            case("conditional_silent", [4, 5, 7], {"score": 5}, established=()),
            case("equivalent_paraphrase", [1, 4, 5, 7], {"score": 5}),
            case("affirmed_unsafe", [1, 4, 5, 7], {"score": 0}, negatives=[1]),
            case("negated_unsafe", [1, 4, 5, 7], {"score": 5}),
            case("evidence_loss", [], {"status": "INCOMPLETE"}),
        ]

    def _approve(self, code, rule):
        spec_set = rubric_specs.spec_set_for_version(DRAFT)
        classification = json.loads(json.dumps(spec_set["classification"]))
        classification["questions"][code]["review_status"] = "APPROVED"
        if rule is not None:
            classification["questions"][code]["rule"] = rule
        approved = {**spec_set, "classification": classification}
        return mock.patch.object(rubric_specs, "spec_set_for_version",
                                 lambda version: approved if version == DRAFT else None)

    def test_approval_alone_does_not_activate_without_a_complete_tested_rule(self):
        rule = dict(self.ILLUSTRATIVE_RULE)
        for candidate in (None, rule, {**rule, "acceptance_cases": self._cases(rule)[:5]}):
            with self._approve("HK-TSK-002", candidate):
                # The question keeps its approved template rule.
                active = rubric_specs.spec_for(DRAFT, "HK-TSK-002")[1]["rule"]
                self.assertEqual(active["rule_version"], "TPL-PROCEDURE.v2")
        # A case whose expected outcome the rule does not produce blocks it.
        wrong = self._cases(rule)
        wrong[1] = {**wrong[1], "expected": {"score": 5}}
        self.assertTrue(rubric_specs.acceptance_gaps({**rule, "acceptance_cases": wrong}))
        complete = {**rule, "acceptance_cases": self._cases(rule)}
        self.assertEqual(rubric_specs.acceptance_gaps(complete), [])
        with self._approve("HK-TSK-002", complete):
            version, spec = rubric_specs.spec_for(DRAFT, "HK-TSK-002")
            self.assertEqual(spec["rule"]["rule_version"], "TEST-1")
            self.assertTrue(version.endswith("+GOV2.0-RC1-core-reviewed-2026-10-22+TEST-1"))
            # Every other question keeps its template rule.
            self.assertTrue(rubric_specs.spec_for(DRAFT, "HK-TSK-003")[0].endswith("+TPL-PROCEDURE.v2"))

    def test_question_rule_predicates(self):
        rule = {**self.ILLUSTRATIVE_RULE, "acceptance_cases": self._cases(self.ILLUSTRATIVE_RULE)}
        with self._approve("HK-TSK-002", rule):
            # Supporting detail missing: still full credit.
            self.assertEqual(run("HK-TSK-002", [1, 4, 5, 7])["score"], Decimal("5.00"))
            # Minimum viable competence with compensable gaps: 3, whatever
            # the number of points missing.
            self.assertEqual(run("HK-TSK-002", [4, 5])["band"], rubric_specs.BAND_PARTIAL)
            # Missing an approved prerequisite: 0, distinct from an unsafe
            # action and not a critical failure.
            missing = run("HK-TSK-002", [1, 2, 3, 5, 6, 7])
            self.assertEqual((missing["score"], missing["band"], missing["critical_failure"]),
                             (Decimal("0.00"), rubric_specs.BAND_PREREQUISITE_MISSING, False))
            self.assertEqual(run("HK-TSK-002", [1, 2, 3, 4, 6, 7])["band"], rubric_specs.BAND_BELOW_MINIMUM)
            # Conditional point ("gloves/PPE as required"): it counts only
            # when its condition is established; silence alone does not
            # establish it, and it is never a hold.
            _, spec = rubric_specs.spec_for(DRAFT, "HK-TSK-002")

            def execute(matched, inapplicable=(), established=()):
                return rubric_specs.execute(spec, max_score=5, matched_ordinals=matched, total_points=7,
                                            negative_ordinals=[], inapplicable_ordinals=inapplicable,
                                            established_ordinals=established)

            self.assertEqual(execute([4, 5, 7], inapplicable=[1])["score"], Decimal("5.00"))
            silent = execute([4, 5, 7])
            self.assertEqual((silent["score"], silent["inapplicable_ordinals"]), (Decimal("5.00"), [1]))
            omitted = execute([4, 5, 7], established=[1])
            self.assertEqual((omitted["score"], omitted["status"]), (Decimal("3.00"), "DEFINED"))
            # Affirmed unsafe action: 0 and critical failure.
            unsafe = run("HK-TSK-002", range(1, 8), negatives=[1])
            self.assertEqual((unsafe["score"], unsafe["critical_failure"]), (Decimal("0.00"), True))

    def test_ecg_tsk_001_four_outcomes_and_applicable_safeguards(self):
        # Decision 5 (22 Oct 2026): the four original outcomes are the
        # denominator; mobility plan (1) and consent (2) are applicable
        # safeguards, never caps and never optional. Their consequence when
        # unevidenced is escalated, so the question is not selected.
        _, spec = rubric_specs.spec_for(DRAFT, "ECG-TSK-001")
        self.assertEqual((spec["denominator"], spec["steps"], spec["safeguards"]), (4, [3, 4, 5, 6], [1, 2]))
        self.assertNotIn("missing_caps", spec)
        self.assertEqual(run("ECG-TSK-001", range(1, 7))["score"], Decimal("10.00"))
        # Safeguards evidenced: scored on the four outcomes alone.
        self.assertEqual(run("ECG-TSK-001", [1, 2, 3, 4, 6])["score"], Decimal("7.50"))
        self.assertEqual(run("ECG-TSK-001", [1, 2, 3])["score"], Decimal("2.50"))
        # A safeguard not evidenced: the escalated conflict, held.
        held = run("ECG-TSK-001", [2, 3, 4, 5, 6])
        self.assertEqual((held["status"], held["unmet_safeguards"]), (rubric_specs.STATUS_PENDING, [1]))
        # An affirmed unsafe action is resolved by the agreed rules: 0.
        unsafe = run("ECG-TSK-001", [3, 4, 5, 6], negatives=[1])
        self.assertEqual((unsafe["score"], unsafe["critical_failure"]), (Decimal("0.00"), True))
        self.assertFalse(rubric_specs.rule_complete(DRAFT, "ECG-TSK-001"))

    def test_templates_are_approved_and_every_question_has_an_active_rule(self):
        classification = rubric_specs.spec_set_for_version(DRAFT)["classification"]
        self.assertEqual(set(classification["templates"]), {
            "TPL-SAFEGUARD", "TPL-INTEGRITY", "TPL-HYGIENE", "TPL-COMMUNICATION", "TPL-PROCEDURE"})
        self.assertEqual({t["status"] for t in classification["templates"].values()}, {"APPROVED"})
        partial_mappings, binary = 0, 0
        for code, entry in classification["questions"].items():
            mapping = entry["mapping"]
            self.assertIn(mapping["template"], classification["templates"], code)
            self.assertTrue(mapping["central"] and set(mapping["central"]) <= set(entry["core"]), code)
            rule = rubric_specs.spec_for(DRAFT, code)[1].get("rule")
            self.assertIsNotNone(rule, code)
            self.assertEqual(rubric_specs.acceptance_gaps(rule), [], code)
            self.assertTrue(rubric_specs.rule_complete(DRAFT, code), code)
            # Decision 2: where every Core outcome was central, partial
            # credit is kept where it can be defined, otherwise binary with a
            # recorded justification.
            if mapping.get("exception") == rubric_specs.EXCEPTION_BINARY:
                binary += 1
                self.assertTrue(mapping["binary_justification"], code)
                self.assertIsNone(rule["partial"], code)
            elif mapping.get("partial"):
                partial_mappings += 1
                self.assertTrue(mapping["partial_rationale"], code)
                self.assertEqual(rule["partial"], mapping["partial"], code)
            else:
                self.assertNotEqual(set(mapping["central"]), set(entry["core"]), code)
                self.assertEqual(rule["partial"], [[o] for o in sorted(mapping["central"])], code)
        self.assertEqual((partial_mappings, binary), (21, 3))
        self.assertTrue(rubric_specs.selection_gate_active(DRAFT))

    def test_all_central_question_without_partial_mapping_or_justification_has_no_rule(self):
        spec_set = rubric_specs.spec_set_for_version(DRAFT)
        classification = json.loads(json.dumps(spec_set["classification"]))
        classification["questions"]["HCA-COM-004"]["mapping"].pop("partial")
        classification["questions"]["WS-SAF-020"]["mapping"]["binary_justification"] = ""
        self.assertIsNone(rubric_specs.template_rule(classification, "HCA-COM-004"))
        self.assertIsNone(rubric_specs.template_rule(classification, "WS-SAF-020"))

    def test_template_scores_by_central_and_compensable_outcomes(self):
        classification = rubric_specs.spec_set_for_version(DRAFT)["classification"]
        entry = classification["questions"]["HK-TSK-002"]
        core, central = entry["core"], entry["mapping"]["central"]
        version, spec = rubric_specs.spec_for(DRAFT, "HK-TSK-002")
        self.assertTrue(version.endswith(f"+{entry['mapping']['template']}.v2"))
        self.assertEqual(run("HK-TSK-002", core)["score"], Decimal("5.00"))      # supporting omitted
        self.assertEqual(run("HK-TSK-002", central)["score"], Decimal("3.00"))   # several compensable gaps
        below = run("HK-TSK-002", [o for o in range(1, 8) if o not in central])
        self.assertEqual((below["score"], below["band"], below["critical_failure"]),
                         (Decimal("0.00"), rubric_specs.BAND_BELOW_MINIMUM, False))

    def test_partial_mappings_of_all_central_questions(self):
        # HCA-COM-004: adapting communication (3) is the central competence;
        # confirming understanding (5) is a compensable gap.
        self.assertEqual(run("HCA-COM-004", [3, 5])["score"], Decimal("5.00"))
        self.assertEqual(run("HCA-COM-004", [3])["score"], Decimal("3.00"))
        self.assertEqual(run("HCA-COM-004", [5, 1, 2])["score"], Decimal("0.00"))
        # NA-SAF-021: stopping the transfer (1) or locking the bed (2) each
        # removes the immediate risk.
        self.assertEqual(run("NA-SAF-021", [1])["score"], Decimal("3.00"))
        self.assertEqual(run("NA-SAF-021", [2])["score"], Decimal("3.00"))
        self.assertEqual(run("NA-SAF-021", [1, 2])["score"], Decimal("5.00"))
        self.assertEqual(run("NA-SAF-021", [3, 4])["score"], Decimal("0.00"))
        # Affirmed unsafe action is still 0 and a critical failure.
        self.assertEqual(run("NA-SAF-021", [1], negatives=[1])["critical_failure"], True)

    def test_binary_question_has_no_partial_band(self):
        # WS-SAF-020: the refusal to ride forks cannot be performed in part.
        self.assertEqual(run("WS-SAF-020", [1, 3])["score"], Decimal("5.00"))
        self.assertEqual(run("WS-SAF-020", [1, 2, 4])["score"], Decimal("0.00"))

    def test_conditional_point_counts_only_when_established(self):
        # HK-BEH-004 point 5 "seek urgent help if immediate danger" is its
        # central outcome and conditional.
        self.assertEqual(rubric_specs.conditional_ordinals(DRAFT, "HK-BEH-004"), [5])
        silent = run("HK-BEH-004", [2, 3, 4])
        self.assertEqual((silent["score"], silent["inapplicable_ordinals"]), (Decimal("5.00"), [5]))
        established = run("HK-BEH-004", [2, 3, 4], established=[5])
        self.assertEqual((established["score"], established["band"]),
                         (Decimal("0.00"), rubric_specs.BAND_BELOW_MINIMUM))
        self.assertEqual(run("HK-BEH-004", [2, 3, 4, 5], established=[5])["score"], Decimal("5.00"))
        # An inapplicable point never stands in for evidence: with no
        # applicable Core outcome evidenced the answer shows no competence.
        self.assertEqual(run("HK-BEH-004", [1])["score"], Decimal("0.00"))
        # HCA-HYG-016 "disinfect if required" (3): partial mapping on the
        # approved product (1); adopted hand hygiene (6) completes it.
        self.assertEqual(run("HCA-HYG-016", [1, 6])["score"], Decimal("5.00"))
        self.assertEqual(run("HCA-HYG-016", [1, 6], established=[3])["score"], Decimal("3.00"))

    def test_additional_core_effects(self):
        # Adopted: HK-TSK-020 point 3 (corners/tracks) is now required for 5.
        self.assertEqual(run("HK-TSK-020", [1, 2, 3])["score"], Decimal("5.00"))
        self.assertEqual(run("HK-TSK-020", [1, 2])["score"], Decimal("3.00"))
        # Not adopted (duplicates Core 2): HK-SAF-002 point 3 stays Supporting.
        self.assertEqual(run("HK-SAF-002", [1, 2, 4, 5])["score"], Decimal("5.00"))

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
        # Decision D (20 Oct 2026): clarifies and adjusts without "calm" = 2.
        self.assertEqual(run("HK-PRO-001", [2, 3])["score"], Decimal("2.00"))
        self.assertEqual(run("HK-PRO-001", [2, 3], negatives=[2])["score"], Decimal("0.00"))
        self.assertEqual(run("HK-PRO-001", [2])["score"], Decimal("0.00"))

    def test_hk_beh_002_untouched_but_unreported_is_3(self):
        # Decision E (20 Oct 2026).
        self.assertEqual(run("HK-BEH-002", [1, 2, 3])["score"], Decimal("5.00"))
        self.assertEqual(run("HK-BEH-002", [1, 2])["score"], Decimal("3.00"))
        unreported = run("HK-BEH-002", [1, 2], negatives=[3])
        self.assertEqual((unreported["score"], unreported["critical_failure"]), (Decimal("3.00"), False))
        takes = run("HK-BEH-002", [2], negatives=[1])
        self.assertEqual((takes["score"], takes["critical_failure"]), (Decimal("0.00"), True))

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

    def _session(self, role_code, lang="EN"):
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
        return session

    def _assess(self, role_code, plan, lang="EN"):
        session = self._session(role_code, lang)
        candidate, tier = session.candidate, session.evaluation_tier
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

    @staticmethod
    def _compensable(code):
        """Core outcomes that are neither in the central competence nor
        conditional: leaving one out is a compensable gap."""
        rule = rubric_specs.spec_for(DRAFT, code)[1].get("rule")
        if not rule or not rule["partial"]:
            return []
        central = {o for group in rule["partial"] for o in group}
        return [o for [o] in rule["full"] if o not in central and o not in rule["conditional"]]

    def test_compensable_gaps_everywhere_earn_partial_credit(self):
        def gaps(code):
            points, _ = all_points(code)
            return points - set(self._compensable(code)), set()

        for lang in ("EN", "AR"):
            with self.subTest(lang=lang):
                evaluation, summary, codes = self._assess("hotel_housekeeper", gaps, lang)
                self.assertEqual(summary.status, SessionEvaluationSummary.STATUS_EVALUATED)
                self.assertGreater(summary.total_score, 0)
                results = {r.question.question_template.question_code: r for r in evaluation.response_results.all()}
                partial = [code for code in codes if self._compensable(code)]
                self.assertTrue(partial)
                for code in partial:
                    # Several Core outcomes missing, central competence shown: 3.
                    self.assertEqual(results[code].score, Decimal("3.00"), code)
                    self.assertEqual(results[code].metadata["rubric_band"], rubric_specs.BAND_PARTIAL)
                # A routine partial answer never raises a review flag.
                self.assertFalse(any(r.requires_human_review for r in results.values()))
                self.assertTrue(all(r.passed_required_indicators for r in results.values()))
                self.assertEqual(summary.metadata["rubric_pending_question_codes"], [])

    def test_undefined_band_holds_the_result_then_recalculates_on_approval(self):
        def two_missing(code):
            points, _ = all_points(code)
            if code not in SPECS["questions"] and len(points) >= 4:
                return points - {max(points), max(points) - 1}, set()
            return points, set()

        # Assessed before the templates were approved: held as pending.
        with without_templates():
            evaluation, summary, _ = self._assess("hotel_housekeeper", two_missing)
        pending = summary.metadata["rubric_pending_question_codes"]
        self.assertTrue(pending)
        # No guessed zero: the answers are unscored and the result is held
        # with its actual cause, not as poor performance.
        self.assertFalse(evaluation.response_results.filter(
            question__question_template__question_code__in=pending).exists())
        self.assertEqual(summary.status, SessionEvaluationSummary.STATUS_PARTIALLY_EVALUATED)
        self.assertEqual(evaluation.readiness_status, ReadinessStatus.INCOMPLETE)
        self.assertIn("awaiting approval", evaluation.readiness_override_reason)

        # The rule templates are approved and deployed: the named, audited
        # re-score of this test assessment finalises it.
        from api.audit.models import AuditLog

        with self.assertRaises(CommandError):  # never a blanket re-score
            call_command("recalculate_pending_rubric_results", verbosity=0)
        call_command("recalculate_pending_rubric_results", evaluation=[str(evaluation.public_id)],
                     run_label="TEST-RUN-1", test_data=True, verbosity=0)
        evaluation.refresh_from_db()
        latest = evaluation.session_summaries.order_by("-generated_at").first()
        self.assertEqual(latest.metadata["rubric_pending_question_codes"], [])
        self.assertNotEqual(evaluation.readiness_status, ReadinessStatus.INCOMPLETE)
        self.assertTrue(AuditLog.objects.filter(description__icontains="after scoring-rule approval").exists())

        # The original records and rule versions are preserved, write-once.
        from api.evaluations.models import ScoreRecalculationSnapshot

        snapshot = ScoreRecalculationSnapshot.objects.get(evaluation=evaluation)
        original = snapshot.before["session_summaries"][0]["fields"]
        self.assertEqual(original["metadata"]["rubric_pending_question_codes"], pending)
        self.assertEqual(original["status"], SessionEvaluationSummary.STATUS_PARTIALLY_EVALUATED)
        self.assertEqual(snapshot.before["evaluation"][0]["fields"]["readiness_status"], ReadinessStatus.INCOMPLETE)
        self.assertEqual(len(snapshot.before["response_results"]), evaluation.response_results.count() - len(pending))
        self.assertEqual(snapshot.rubric_spec_versions, [SPECS["spec_version"]])
        self.assertTrue(snapshot.rule_set_versions)
        self.assertEqual(snapshot.after["pending"], [])
        # The re-score records the template rule versions that decided it.
        self.assertTrue(any(".v2" in version for version in snapshot.after["rubric_spec_versions"]))
        snapshot.before = {}
        with self.assertRaises(ValueError):
            snapshot.save()
        # Release still passes its checks: the certificate follows the
        # normal eligibility rule for the recalculated outcome.
        report = evaluation.reports.get(report_status="ACTIVE")
        self.assertEqual(report.overall_percentage, latest.overall_percentage)
        self.assertEqual(snapshot.run_label, "TEST-RUN-1")
        # The normal eligibility rule decides the certificate automatically;
        # a test assessment's certificate is marked as a test document.
        from api.core.constants import CertificateStatus

        eligible, _ = certificate_eligibility(evaluation, latest)
        if eligible:
            self.assertEqual(evaluation.certificate_status, CertificateStatus.ISSUED)
            self.assertTrue(evaluation.certificate.is_test_document)
        else:
            self.assertNotEqual(evaluation.certificate_status, CertificateStatus.ISSUED)

    def test_selection_draws_only_complete_rules_once_templates_are_approved(self):
        evaluation, summary, codes = self._assess("elderly_caregiver", lambda code: (all_points(code)[0], set()))
        self.assertTrue(all(rubric_specs.rule_complete(DRAFT, code) for code in codes), codes)
        self.assertNotIn("ECG-TSK-001", codes)
        self.assertEqual(summary.metadata["rubric_pending_question_codes"], [])

    def test_coverage_never_overrides_rule_completeness(self):
        # Decision 3 (22 Oct 2026): when the complete rules cannot meet the
        # approved coverage, the session is not started on an unresolved
        # rule; the shortfall is shown as an availability exception.
        from api.audit.models import AuditLog
        from api.interviews.question_allocation import AllocationGap

        session = self._session("elderly_caregiver")
        real = rubric_specs.rule_complete
        with mock.patch.object(rubric_specs, "rule_complete",
                               lambda version, code: "-SAF-" not in code and real(version, code)):
            with self.assertRaises(AllocationGap) as raised:
                QuestionGenerationService.generate_questions(session)
        self.assertIn("Rubric availability exception", raised.exception.gaps[0])
        self.assertIn("ECG-TSK-001", raised.exception.gaps[0])
        self.assertFalse(session.questions.exists())
        logged = AuditLog.objects.filter(description__icontains="selection gap").latest("created_at")
        self.assertIn("Rubric availability exception", logged.data["gaps"][0])

    def test_interpreter_marks_conditional_points_and_needs_quoted_basis(self):
        from api.questions.models import IndicatorDefinition

        indicators = list(IndicatorDefinition.objects.filter(question_code="HK-BEH-004", bank_version=DRAFT))
        items = {item["id"]: item for item in ResponseInterpretationService._indicator_prompt_items(indicators)}
        conditional = [i for i, item in items.items() if item.get("conditional")]
        self.assertEqual(len(conditional), 1)
        self.assertTrue(conditional[0].endswith("-MI-05") or conditional[0].endswith("-MI-5"), conditional)
        unconditional = next(i for i, item in items.items() if item["type"] == "must_include" and i not in conditional)
        policy = ResponseInterpretationService.apply_indicator_policy([], indicators, [], [
            {"indicator_id": conditional[0], "basis": "question", "quote": ""},   # no quote
            {"indicator_id": conditional[0], "basis": "assumed", "quote": "x"},   # no stated basis
            {"indicator_id": unconditional, "basis": "answer", "quote": "x"},     # not conditional
        ])
        self.assertEqual(policy["condition_established_ids"], [])
        policy = ResponseInterpretationService.apply_indicator_policy([], indicators, [], [
            {"indicator_id": conditional[0], "basis": "question", "quote": "immediate safety"}])
        self.assertEqual(policy["condition_established_ids"], conditional)

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
