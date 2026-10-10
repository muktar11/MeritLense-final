"""GOV2.0-DRAFT question bank package: package integrity, competency
allocation selection, and proof that loading the draft leaves the live
GOV1.2 bank, live interviews and live scoring untouched."""
import json
import random
from collections import Counter
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from api.accounts.models import Company, User
from api.candidates.models import Candidate
from api.core.constants import InterviewEvaluationTier, QuestionLifecycleStatus, Roles
from api.dashboard.comparison_services import _current_rule_set_ids
from api.evaluations.models import ScoringRuleSet
from api.interviews.management.commands.import_question_bank_draft import Command as DraftImport, split_points
from api.interviews.models import InterviewConfiguration, InterviewRubric
from api.interviews.question_allocation import (
    FULL, SCREENING, AllocationGap, QuestionCandidate, critical_competencies, select_questions,
)
from api.questions.models import QuestionTemplate
from api.sessions.models import InterviewSession
from api.sessions.services import QuestionGenerationService

BANK = Path(__file__).resolve().parent / "fixtures" / "question_bank"
V12 = json.loads((BANK / "governance_v1_2_corrected.json").read_text(encoding="utf-8"))
V2 = json.loads((BANK / "governance_v2_0_draft.json").read_text(encoding="utf-8"))
LIVE = "GOV1.2-FINAL"
DRAFT = "GOV2.0-DRAFT"


class PackageIntegrityTests(SimpleTestCase):
    def test_package_is_a_draft_with_2100_unique_questions_100_per_role(self):
        self.assertEqual(V2["version"], DRAFT)
        self.assertTrue(V2["status"].startswith("DRAFT"))
        codes = [q["question_code"] for q in V2["questions"]]
        self.assertEqual(len(codes), 2100)
        self.assertEqual(len(set(codes)), 2100)
        self.assertEqual(set(Counter(q["role"] for q in V2["questions"]).values()), {100})

    def test_every_approved_question_is_carried_with_its_approved_sets_and_scoring(self):
        v2 = {q["question_code"]: q for q in V2["questions"]}
        for live in V12["questions"]:
            q = v2[live["question_code"]]
            self.assertEqual(q["source"], "approved_v1_2")
            self.assertEqual(q["v1_2_full_set"], live["full_flag"] == "Yes")
            self.assertEqual(q["v1_2_screening_set"], live["is_screening"])
            self.assertEqual(q["score_note"], live["score_note"])
            self.assertEqual(q["competency_code"], live["competency_code"])

    def test_pending_criticality_changes_are_held_at_the_approved_value(self):
        v12 = {q["question_code"]: q for q in V12["questions"]}
        held = {q["question_code"]: q for q in V2["questions"] if "proposed_criticality" in q}
        self.assertEqual(set(held), {"SNC-GEN-005", "CCG-PRO-004", "ECG-PRO-004", "RS-BAD-003"})
        for code, q in held.items():
            self.assertEqual(q["criticality"], v12[code]["criticality"])
            self.assertEqual(q["proposed_criticality"], "Critical")

    def test_communication_ability_is_non_critical_in_the_three_added_roles(self):
        for role in ("Commercial Cleaner", "General Labor", "Skilled Trades"):
            levels = {q["criticality"] for q in V2["questions"] if q["role"] == role and q["competency_code"] == "communication_ability"}
            self.assertEqual(levels, {"Non-Critical"})

    def test_new_questions_use_the_5_3_0_framework(self):
        from api.interviews.management.commands.import_governance_question_bank import _scoring_shape

        for q in V2["questions"]:
            if q["source"] == "expansion":
                self.assertEqual(_scoring_shape(q["score_note"])["scoring_type"], "0/3/5", q["question_code"])

    def test_allocation_matrix_is_exactly_21_and_7_and_covers_every_critical_competency(self):
        for profile in V2["role_profiles"]:
            critical = critical_competencies(profile)
            self.assertEqual(sum(profile["allocation"][FULL].values()), 21, profile["role"])
            self.assertEqual(sum(profile["allocation"][SCREENING].values()), 7, profile["role"])
            for tier in (FULL, SCREENING):
                self.assertTrue(critical <= set(profile["allocation"][tier]), (profile["role"], tier))

    def test_package_passes_the_importer_validation(self):
        DraftImport.validate(V2)

    def test_importer_refuses_a_non_draft_package(self):
        with self.assertRaises(CommandError):
            DraftImport.validate({**V2, "status": "FINAL"})
        with self.assertRaises(CommandError):
            DraftImport.validate({**V2, "version": LIVE})

    def test_arabic_semicolon_is_split(self):
        self.assertEqual(split_points("أ؛ ب؛ ج", "AR"), ["أ", "ب", "ج"])
        self.assertEqual(split_points("a; b, c"), ["a", "b, c"])


def _pool(role, language="EN"):
    return [QuestionCandidate.from_fixture(q, language) for q in V2["questions"] if q["role"] == role]


def _profile(role):
    return next(p for p in V2["role_profiles"] if p["role"] == role)


class AllocationSelectionTests(SimpleTestCase):
    role = "Nursing Assistant"
    role_code = "nursing_assistant"

    def _select(self, tier, **kwargs):
        profile = _profile(self.role)
        params = dict(
            pool=_pool(self.role, kwargs.pop("pool_language", "EN")), allocation=profile["allocation"][tier],
            critical=critical_competencies(profile), tier=tier, role_code=self.role_code, language="EN",
            rng=random.Random(1),
        )
        params.update(kwargs)
        return select_questions(**params)

    def test_full_and_screening_get_exactly_their_allocation(self):
        for tier, n in ((FULL, 21), (SCREENING, 7)):
            result = self._select(tier)
            self.assertEqual(len(result.questions), n)
            self.assertEqual(Counter(q.competency_code for q in result.questions), Counter(_profile(self.role)["allocation"][tier]))

    def test_screening_only_draws_screening_eligible_questions(self):
        self.assertTrue(all(q.screening_eligible for q in self._select(SCREENING).questions))

    def test_wrong_language_or_role_is_never_selected(self):
        with self.assertRaises(AllocationGap):
            self._select(FULL, pool_language="AR")  # pool is Arabic, session is English
        with self.assertRaises(AllocationGap):
            self._select(FULL, role_code="driver")

    def test_skill_matching_never_changes_the_competency_allocation(self):
        skilled = self._select(FULL, candidate_skills=["medication reporting", "hand hygiene", "patient lifting"])
        self.assertEqual(Counter(q.competency_code for q in skilled.questions), Counter(_profile(self.role)["allocation"][FULL]))

    def test_retake_uses_unseen_alternatives_when_the_pool_allows(self):
        first = self._select(FULL)
        retake = self._select(FULL, previous_codes=[q.code for q in first.questions], rng=random.Random(2))
        self.assertEqual(retake.reused_codes, [])
        self.assertFalse({q.code for q in first.questions} & {q.code for q in retake.questions})

    def test_retake_reuses_only_what_the_pool_cannot_replace(self):
        # Elderly Caregiver: 12 Behavioral questions per Full, 14 in the pool.
        profile = _profile("Elderly Caregiver")
        kwargs = dict(pool=_pool("Elderly Caregiver"), allocation=profile["allocation"][FULL],
                      critical=critical_competencies(profile), tier=FULL, role_code="elderly_caregiver", language="EN")
        first = select_questions(**kwargs, rng=random.Random(1))
        retake = select_questions(**kwargs, previous_codes=[q.code for q in first.questions], rng=random.Random(2))
        self.assertEqual(len(retake.reused_codes), 12 - (14 - 12))
        self.assertEqual(len(retake.questions), 21)

    def test_a_pool_too_small_raises_a_gap_instead_of_bypassing_the_matrix(self):
        pool = [q for q in _pool(self.role) if q.competency_code != "hygiene_standards"]
        with self.assertRaises(AllocationGap) as ctx:
            self._select(FULL, pool=pool)
        self.assertIn("hygiene_standards", str(ctx.exception))

    def test_a_matrix_missing_a_critical_competency_or_wrong_total_is_rejected(self):
        allocation = dict(_profile(self.role)["allocation"][FULL])
        allocation["behavior_integrity"] += allocation.pop("hygiene_standards")
        with self.assertRaises(AllocationGap):
            self._select(FULL, allocation=allocation)
        with self.assertRaises(AllocationGap):
            self._select(FULL, allocation={**_profile(self.role)["allocation"][FULL], "behavior_integrity": 9})


class DraftImportIsolationTests(TestCase):
    """Loading GOV2.0-DRAFT next to the live bank must not change what live
    interviews ask, how they are scored, or what Comparison compares."""

    @classmethod
    def setUpTestData(cls):
        call_command("import_governance_question_bank", verbosity=0)
        cls.live_active_templates = set(QuestionTemplate.objects.filter(is_active=True).values_list("id", flat=True))
        cls.configs_before = list(InterviewConfiguration.objects.order_by("id").values())
        cls.rubrics_before = InterviewRubric.objects.count()
        cls.active_rule_sets_before = set(ScoringRuleSet.objects.filter(is_active=True).values_list("id", flat=True))
        call_command("import_question_bank_draft", verbosity=0)
        cls.owner = User.objects.create_user(email="v2@example.com", password="x", first_name="V", last_name="Two",
                                             role=Roles.B2B, is_verified=True)
        cls.company = Company.objects.create(name="V2 Co", registration_number="V2-1", company_size="11-50",
                                             phone_number="+1", country="X", city="Y", admin_user=cls.owner)

    def test_draft_is_loaded_but_entirely_inactive(self):
        draft = QuestionTemplate.objects.filter(question_set_version=DRAFT)
        self.assertEqual(draft.count(), 4200)
        self.assertFalse(draft.filter(is_active=True).exists())
        self.assertEqual(set(draft.values_list("question_status", flat=True)), {QuestionLifecycleStatus.DRAFT})
        draft_rule_sets = ScoringRuleSet.objects.filter(version=V2["rule_set_version"])
        self.assertEqual(draft_rule_sets.count(), 42)
        self.assertFalse(draft_rule_sets.filter(is_active=True).exists())

    def test_live_bank_configs_rubrics_and_rule_sets_are_unchanged(self):
        self.assertEqual(set(QuestionTemplate.objects.filter(is_active=True).values_list("id", flat=True)), self.live_active_templates)
        self.assertEqual(list(InterviewConfiguration.objects.order_by("id").values()), self.configs_before)
        self.assertEqual(InterviewRubric.objects.count(), self.rubrics_before)
        self.assertEqual(set(ScoringRuleSet.objects.filter(is_active=True).values_list("id", flat=True)), self.active_rule_sets_before)

    def test_live_interviews_still_draw_only_live_questions(self):
        for tier in (InterviewEvaluationTier.FULL, InterviewEvaluationTier.SCREENING):
            for lang in ("EN", "AR"):
                config = InterviewConfiguration.objects.get(role_code="driver", language=lang, evaluation_tier=tier,
                                                           question_set_version=LIVE)
                cand = Candidate.objects.create(first_name="C", last_name=f"{tier}{lang}", email=f"v2-{tier}-{lang}@example.com",
                                                passport_id=f"V2{tier}{lang}", job_role="DR", preferred_language=lang,
                                                passport_document="x.pdf", created_by=self.owner, company=self.company)
                session = InterviewSession.objects.create(
                    candidate=cand, organization=self.company, config=config, role_name=config.role_name,
                    role_code="driver", ui_language=lang, candidate_language=lang, evaluation_tier=tier,
                    total_questions=config.total_questions, rubric_version=LIVE, question_set_version=LIVE,
                    expires_at=InterviewSession.build_expiry(30), created_by=self.owner, started_at=timezone.now())
                questions = QuestionGenerationService.generate_questions(session)
                self.assertEqual(len(questions), config.total_questions)
                self.assertEqual({q.question_template.question_set_version for q in questions}, {LIVE})

    def test_comparison_still_treats_only_live_rule_sets_as_current(self):
        for tier_ids in [_current_rule_set_ids("COMPANY", self.company, "driver")]:
            versions = set(ScoringRuleSet.objects.filter(id__in=tier_ids).values_list("version", flat=True))
            self.assertEqual(versions, {"governance-v1.2-corrected"})

    def test_reloading_the_draft_is_idempotent(self):
        call_command("import_question_bank_draft", verbosity=0)
        self.assertEqual(QuestionTemplate.objects.filter(question_set_version=DRAFT).count(), 4200)
        self.assertEqual(ScoringRuleSet.objects.filter(version=V2["rule_set_version"]).count(), 42)
