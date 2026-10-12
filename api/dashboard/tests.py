from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from api.accounts.models import Company, CompanyEmployerProfile, TeamMemberProfile, User
from api.candidates.models import Candidate
from api.core.constants import CompanyTeamPermissions, EvaluationType, InterviewEvaluationTier, Roles
from api.dashboard.dashboard_layout import DEFAULT_WIDGETS
from api.dashboard.comparison_services import (
    build_full_comparison,
    compute_key_differences,
    get_comparable_roles,
    get_eligible_candidates,
)
from api.evaluations.models import (
    Evaluation,
    EvaluationReadinessCorrection,
    ScoringRuleSet,
    SessionEvaluationSummary,
)
from api.interviews.models import InterviewConfiguration
from api.payments.models import Customer, Invoice, Payment, Price, Subscription
from api.reports.models import EvaluationReport
from api.sessions.models import InterviewSession


class CandidateComparisonApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _make_summary(self, *, candidate, created_by, company, overall_percentage, competencies):
        """Real SessionEvaluationSummary fixture - the actual, currently-
        populated scoring pipeline output the comparison endpoint reads
        from, not the legacy ScoreSet/CandidateScore models."""
        config = InterviewConfiguration.objects.create(
            role_name="Housekeeper",
            role_code="domestic_worker",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=45,
            total_questions=1,
            allow_retries=True,
            max_retries=1,
            rubric_version="v2.0",
            question_set_version="v1.2",
        )
        session = InterviewSession.objects.create(
            candidate=candidate,
            organization=company,
            config=config,
            role_name=config.role_name,
            role_code=config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=created_by,
        )
        evaluation = Evaluation.objects.create(
            session=session,
            candidate=candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=45,
            created_by=created_by,
        )
        rule_set = ScoringRuleSet.objects.create(
            name=f"Compare Rules {candidate.pk}",
            version="v1",
            role_code="domestic_worker",
            role_name="Housekeeper",
            evaluation_tier=InterviewEvaluationTier.FULL,
            is_active=True,
            created_by=created_by,
            company=company,
        )
        competencies_summary = [
            {
                "competency_code": code,
                "competency_name": code,
                "percentage": value,
                "status": "EVALUATED",
                "response_count": 1,
                "completed_response_count": 1,
            }
            for code, value in competencies.items()
        ]
        return SessionEvaluationSummary.objects.create(
            evaluation=evaluation,
            session=session,
            candidate=candidate,
            rule_set=rule_set,
            total_score=Decimal(str(overall_percentage)),
            max_score=Decimal("100"),
            overall_percentage=Decimal(str(overall_percentage)),
            competencies_summary=competencies_summary,
            status=SessionEvaluationSummary.STATUS_EVALUATED,
        )

    def test_b2c_comparison_with_candidate_ids_returns_exactly_those_candidates_including_unscored(self):
        user = User.objects.create_user(
            email="b2c-compare@example.com",
            password="testpass123",
            first_name="B2C",
            last_name="User",
            role=Roles.B2C,
            is_verified=True,
        )
        self.client.force_authenticate(user)

        scored = Candidate.objects.create(
            first_name="Scored",
            last_name="Candidate",
            email="scored@example.com",
            passport_id="CMP-001",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=user,
        )
        unscored = Candidate.objects.create(
            first_name="Unscored",
            last_name="Candidate",
            email="unscored@example.com",
            passport_id="CMP-002",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=user,
        )
        # A third candidate that exists but is NOT requested - must not appear.
        Candidate.objects.create(
            first_name="Other",
            last_name="Candidate",
            email="other@example.com",
            passport_id="CMP-003",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=user,
        )

        self._make_summary(
            candidate=scored,
            created_by=user,
            company=None,
            overall_percentage=Decimal("82.50"),
            competencies={"communication": 90, "reliability": 75},
        )

        scored_id, unscored_id = str(scored.public_id), str(unscored.public_id)
        response = self.client.get(
            "/api/v1/dashboard/b2c/candidate-comparison",
            {"candidate_ids": f"{scored_id},{unscored_id}"},
        )

        self.assertEqual(response.status_code, 200)
        by_id = {item["candidate_id"]: item for item in response.data}
        self.assertEqual(set(by_id.keys()), {scored_id, unscored_id})
        self.assertEqual(by_id[scored_id]["average_score"], 82.5)
        self.assertEqual(by_id[scored_id]["scores_by_area"]["Communication Ability"], 90.0)
        # Unscored candidate must still be present, not silently dropped.
        self.assertEqual(by_id[unscored_id]["average_score"], 0)
        self.assertEqual(by_id[unscored_id]["scores_by_area"], {})

    def test_b2c_comparison_without_candidate_ids_keeps_original_leaderboard_behavior(self):
        user = User.objects.create_user(
            email="b2c-leaderboard@example.com",
            password="testpass123",
            first_name="B2C",
            last_name="User",
            role=Roles.B2C,
            is_verified=True,
        )
        self.client.force_authenticate(user)

        scored = Candidate.objects.create(
            first_name="Scored",
            last_name="Candidate",
            email="scored2@example.com",
            passport_id="CMP-004",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=user,
        )
        unscored = Candidate.objects.create(
            first_name="Unscored",
            last_name="Candidate",
            email="unscored2@example.com",
            passport_id="CMP-005",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=user,
        )
        self._make_summary(
            candidate=scored,
            created_by=user,
            company=None,
            overall_percentage=Decimal("60.00"),
            competencies={"communication": 60},
        )

        response = self.client.get("/api/v1/dashboard/b2c/candidate-comparison")

        self.assertEqual(response.status_code, 200)
        ids = [item["candidate_id"] for item in response.data]
        self.assertIn(str(scored.public_id), ids)
        # Original behavior: unscored candidates are excluded entirely.
        self.assertNotIn(str(unscored.public_id), ids)

    def test_b2b_comparison_with_candidate_ids_scopes_to_company_and_includes_unscored(self):
        user = User.objects.create_user(
            email="b2b-compare@example.com",
            password="testpass123",
            first_name="B2B",
            last_name="User",
            role=Roles.B2B,
            is_verified=True,
        )
        company = Company.objects.create(
            name="Compare Co",
            registration_number="COMPARE-001",
            company_size="11-50",
            industry="Care",
            phone_number="+251900000099",
            country="Ethiopia",
            city="Addis Ababa",
            admin_user=user,
        )
        CompanyEmployerProfile.objects.create(
            user=user,
            company_name=company.name,
            company_registration_number=company.registration_number,
            company_size=company.company_size,
            company=company,
        )
        self.client.force_authenticate(user)

        scored = Candidate.objects.create(
            first_name="Scored",
            last_name="Candidate",
            email="b2b-scored@example.com",
            passport_id="CMP-006",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=user,
            company=company,
        )
        unscored = Candidate.objects.create(
            first_name="Unscored",
            last_name="Candidate",
            email="b2b-unscored@example.com",
            passport_id="CMP-007",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=user,
            company=company,
        )
        self._make_summary(
            candidate=scored,
            created_by=user,
            company=company,
            overall_percentage=Decimal("77.00"),
            competencies={"teamwork": 77},
        )

        scored_id, unscored_id = str(scored.public_id), str(unscored.public_id)
        response = self.client.get(
            "/api/v1/dashboard/b2b/candidate-comparison",
            {"candidate_ids": f"{scored_id},{unscored_id}"},
        )

        self.assertEqual(response.status_code, 200)
        by_id = {item["candidate_id"]: item for item in response.data}
        self.assertEqual(set(by_id.keys()), {scored_id, unscored_id})
        self.assertEqual(by_id[scored_id]["scores_by_area"]["Teamwork"], 77.0)


DIMENSION_ORDER = ("SAFETY", "HYGIENE", "COMMUNICATION", "PRACTICAL_TASKS", "BEHAVIORAL")
DIMENSION_LABELS = {
    "SAFETY": "Safety Awareness",
    "HYGIENE": "Hygiene & Cleanliness",
    "COMMUNICATION": "Communication Ability",
    "PRACTICAL_TASKS": "Practical Task Execution",
    "BEHAVIORAL": "Behavioral Indicators",
}


class CandidateComparisonFullFlowApiTests(TestCase):
    """The new role-based Candidate Comparison flow (Select Job Role ->
    Select 2-4 Eligible Candidates -> Compare), distinct from the older
    leaderboard-widget comparison covered by CandidateComparisonApiTests
    above, which this module leaves untouched."""

    def setUp(self):
        self.client = APIClient()

    def _make_candidate(self, *, created_by, company, suffix):
        return Candidate.objects.create(
            first_name=f"Cand{suffix}",
            last_name="Test",
            email=f"cand{suffix}@example.com",
            passport_id=f"CMPF-{suffix}",
            job_role="NA",
            core_skills="care",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=created_by,
            company=company,
        )

    def _make_scored_candidate(
        self, *, created_by, company, suffix, role_code, dimension_percentages,
        readiness_status="READY", requires_human_review=False, assessed=5, required=5,
        not_applicable=None, rule_set=None,
    ):
        """Builds a full SessionEvaluationSummary plus an ACTIVE
        EvaluationReport with a hand-crafted report_payload in the exact
        shape EvaluationReportService._build_critical_competency_status
        produces, so build_full_comparison can be exercised end-to-end
        without re-running the full scoring/report-generation pipeline.

        `rule_set`: pass the SAME ScoringRuleSet instance across multiple
        candidates meant to be comparable (the real-world default - one
        active rule set per role) - comparison now only treats candidates
        scored under the current rule set for that role as eligible (see
        _current_rule_set_id), so two independently-created rule sets for
        the same role_code, as this helper used to always do, are
        deliberately treated as two incompatible versions and would make
        the earlier-created candidate silently ineligible."""
        candidate = self._make_candidate(created_by=created_by, company=company, suffix=suffix)
        not_applicable = not_applicable or set()
        config = InterviewConfiguration.objects.create(
            role_name=role_code.replace("_", " ").title(),
            role_code=role_code,
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=45,
            total_questions=1,
            allow_retries=True,
            max_retries=1,
            rubric_version="v2.0",
            question_set_version="v1.2",
        )
        session = InterviewSession.objects.create(
            candidate=candidate,
            organization=company,
            config=config,
            role_name=config.role_name,
            role_code=config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=created_by,
        )
        evaluation = Evaluation.objects.create(
            session=session,
            candidate=candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=45,
            created_by=created_by,
        )
        if rule_set is None:
            rule_set = ScoringRuleSet.objects.create(
                name=f"Compare Rules {candidate.pk}",
                version="v1",
                role_code=role_code,
                role_name=config.role_name,
                evaluation_tier=InterviewEvaluationTier.FULL,
                is_active=True,
                created_by=created_by,
                company=company,
            )
        competencies_summary = [
            {
                "competency_code": dim,
                "competency_name": DIMENSION_LABELS[dim],
                "percentage": pct,
                "status": "EVALUATED",
                "response_count": 1,
                "completed_response_count": 1,
            }
            for dim, pct in dimension_percentages.items()
        ]
        summary = SessionEvaluationSummary.objects.create(
            evaluation=evaluation,
            session=session,
            candidate=candidate,
            rule_set=rule_set,
            total_score=Decimal("0"),
            max_score=Decimal("100"),
            overall_percentage=Decimal("0"),
            competencies_summary=competencies_summary,
            status=SessionEvaluationSummary.STATUS_EVALUATED,
        )
        critical_competency_status = []
        for dim in DIMENSION_ORDER:
            if dim in not_applicable:
                critical_competency_status.append({
                    "label": DIMENSION_LABELS[dim],
                    "status_label": "N/A",
                    "tone": "neutral",
                    "not_applicable": True,
                    "percentage": 0,
                })
            else:
                critical_competency_status.append({
                    "label": DIMENSION_LABELS[dim],
                    "status_label": "Evaluated",
                    "tone": "positive",
                    "not_applicable": False,
                    "percentage": dimension_percentages.get(dim, 0),
                })
        EvaluationReport.objects.create(
            evaluation=evaluation,
            session=session,
            candidate=candidate,
            report_number=f"RPT-TEST-{candidate.pk}",
            report_status=EvaluationReport.STATUS_ACTIVE,
            readiness_status=readiness_status,
            requires_human_review=requires_human_review,
            report_payload={
                "assessment_context": {
                    "competencies_assessed_count": assessed,
                    "competencies_required_count": required,
                },
                "critical_competency_status": critical_competency_status,
            },
        )
        return candidate, summary

    def _make_rule_set(self, *, created_by, company, role_code):
        """One shared, current ScoringRuleSet for candidates in a test that
        are meant to be comparable - pass its result as `rule_set=` to each
        _make_scored_candidate call for that role."""
        return ScoringRuleSet.objects.create(
            name=f"Compare Rules {role_code} {created_by.pk}",
            version="v1",
            role_code=role_code,
            role_name=role_code.replace("_", " ").title(),
            evaluation_tier=InterviewEvaluationTier.FULL,
            is_active=True,
            created_by=created_by,
            company=company,
        )

    # -- get_comparable_roles / get_eligible_candidates --------------------

    def test_full_assessment_candidates_stay_comparable_when_a_screening_rule_set_exists(self):
        """The approved question bank creates a role's Full rule set and
        then its Screening rule set. Comparison used to treat only the
        newest one per role (Screening) as current, so every Full
        Assessment candidate was silently excluded."""
        from api.dashboard.comparison_services import (
            build_full_comparison, get_comparable_roles, get_eligible_candidates,
        )

        user = User.objects.create_user(
            email="tiers-b2c@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        full_rules = self._make_rule_set(created_by=user, company=None, role_code="driver")
        screening_rules = ScoringRuleSet.objects.create(
            name="Driver Screening", version="v1", role_code="driver", role_name="Driver",
            evaluation_tier=InterviewEvaluationTier.SCREENING, is_active=True, created_by=user,
        )
        a, _ = self._make_scored_candidate(created_by=user, company=None, suffix="tier-a", role_code="driver",
                                           dimension_percentages={"SAFETY": 80}, rule_set=full_rules)
        b, _ = self._make_scored_candidate(created_by=user, company=None, suffix="tier-b", role_code="driver",
                                           dimension_percentages={"SAFETY": 60}, rule_set=full_rules)
        c, _ = self._make_scored_candidate(created_by=user, company=None, suffix="tier-c", role_code="driver",
                                           dimension_percentages={"SAFETY": 70}, rule_set=screening_rules)

        self.assertEqual(get_comparable_roles(owner_type="USER", owner=user)[0]["candidate_count"], 3)
        eligible = {row["candidate_id"] for row in get_eligible_candidates(owner_type="USER", owner=user, role_code="driver")}
        self.assertEqual(eligible, {str(a.public_id), str(b.public_id), str(c.public_id)})

        entries = build_full_comparison(owner_type="USER", owner=user, role_code="driver",
                                        candidate_ids=[str(a.public_id), str(b.public_id)], language="en", actor=user)
        self.assertEqual([e["evaluation_tier"] for e in entries], ["FULL", "FULL"])

    def test_get_comparable_roles_groups_by_role_and_counts_distinct_candidates(self):
        user = User.objects.create_user(
            email="roles-b2c@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        driver_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        self._make_scored_candidate(
            created_by=user, company=None, suffix="r1", role_code="driver",
            dimension_percentages={"SAFETY": 80}, not_applicable={"HYGIENE"}, rule_set=driver_rule_set,
        )
        self._make_scored_candidate(
            created_by=user, company=None, suffix="r2", role_code="driver",
            dimension_percentages={"SAFETY": 70}, not_applicable={"HYGIENE"}, rule_set=driver_rule_set,
        )
        self._make_scored_candidate(
            created_by=user, company=None, suffix="r3", role_code="domestic_worker",
            dimension_percentages={"SAFETY": 60},
        )

        roles = get_comparable_roles(owner_type="USER", owner=user)

        by_code = {r["role_code"]: r for r in roles}
        self.assertEqual(by_code["driver"]["candidate_count"], 2)
        self.assertEqual(by_code["domestic_worker"]["candidate_count"], 1)
        self.assertEqual(by_code["driver"]["role_name"], "Driver")

    def test_get_eligible_candidates_excludes_other_roles_and_unscored(self):
        user = User.objects.create_user(
            email="eligible-b2c@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        driver, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="e1", role_code="driver",
            dimension_percentages={"SAFETY": 80}, not_applicable={"HYGIENE"},
        )
        self._make_scored_candidate(
            created_by=user, company=None, suffix="e2", role_code="domestic_worker",
            dimension_percentages={"SAFETY": 80},
        )
        self._make_candidate(created_by=user, company=None, suffix="e3")  # never evaluated

        eligible = get_eligible_candidates(owner_type="USER", owner=user, role_code="driver")

        self.assertEqual([c["candidate_id"] for c in eligible], [str(driver.public_id)])

    # -- build_full_comparison / _competency_rows --------------------------

    def test_build_full_comparison_driver_role_splits_critical_and_excludes_na_dimension(self):
        user = User.objects.create_user(
            email="driver-b2c@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        driver_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        alice, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="d1", role_code="driver",
            dimension_percentages={"SAFETY": 90, "PRACTICAL_TASKS": 80, "BEHAVIORAL": 70, "COMMUNICATION": 60},
            not_applicable={"HYGIENE"}, rule_set=driver_rule_set,
            readiness_status="READY", assessed=4, required=4,
        )
        bob, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="d2", role_code="driver",
            dimension_percentages={"SAFETY": 50, "PRACTICAL_TASKS": 60, "BEHAVIORAL": 85, "COMMUNICATION": 90},
            not_applicable={"HYGIENE"}, rule_set=driver_rule_set,
            readiness_status="PARTIALLY_READY", requires_human_review=True, assessed=3, required=4,
        )

        entries = build_full_comparison(
            owner_type="USER", owner=user, role_code="driver",
            candidate_ids=[str(alice.public_id), str(bob.public_id)],
            language="en", actor=user,
        )

        self.assertEqual(len(entries), 2)
        alice_entry = next(e for e in entries if e["candidate_id"] == str(alice.public_id))
        bob_entry = next(e for e in entries if e["candidate_id"] == str(bob.public_id))

        self.assertEqual(alice_entry["readiness_display"], "Ready")
        self.assertEqual(alice_entry["assessment_coverage"], 100)
        self.assertFalse(alice_entry["requires_human_review"])

        self.assertEqual(bob_entry["readiness_display"], "Partially Ready")
        self.assertEqual(bob_entry["assessment_coverage"], 75)
        self.assertTrue(bob_entry["requires_human_review"])

        labels = {row["label"] for row in alice_entry["competencies"]}
        self.assertNotIn(DIMENSION_LABELS["HYGIENE"], labels)
        self.assertEqual(len(alice_entry["competencies"]), 4)

        classification_by_dim = {row["dimension_key"]: row["classification"] for row in alice_entry["competencies"]}
        self.assertEqual(classification_by_dim["SAFETY"], "CRITICAL")
        self.assertEqual(classification_by_dim["PRACTICAL_TASKS"], "CRITICAL")
        self.assertEqual(classification_by_dim["BEHAVIORAL"], "NON_CRITICAL")
        self.assertEqual(classification_by_dim["COMMUNICATION"], "NON_CRITICAL")

    def test_build_full_comparison_non_driver_role_shows_required_only_no_fabricated_split(self):
        user = User.objects.create_user(
            email="domestic-b2c@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        dw_rule_set = self._make_rule_set(created_by=user, company=None, role_code="domestic_worker")
        alice, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="n1", role_code="domestic_worker",
            dimension_percentages={"SAFETY": 90, "PRACTICAL_TASKS": 80, "BEHAVIORAL": 70}, rule_set=dw_rule_set,
        )
        bob, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="n2", role_code="domestic_worker",
            dimension_percentages={"SAFETY": 60, "PRACTICAL_TASKS": 95, "BEHAVIORAL": 65}, rule_set=dw_rule_set,
        )

        entries = build_full_comparison(
            owner_type="USER", owner=user, role_code="domestic_worker",
            candidate_ids=[str(alice.public_id), str(bob.public_id)],
            language="en", actor=user,
        )

        alice_entry = next(e for e in entries if e["candidate_id"] == str(alice.public_id))
        # domestic_worker has no ROLE_COMPETENCY_CONFIG entry, so no
        # dimension may show a fabricated Critical/Non-Critical split -
        # only HYGIENE/COMMUNICATION are excluded (not required for this
        # role), and the 3 remaining dimensions all read "REQUIRED".
        labels = {row["label"] for row in alice_entry["competencies"]}
        self.assertNotIn(DIMENSION_LABELS["HYGIENE"], labels)
        self.assertNotIn(DIMENSION_LABELS["COMMUNICATION"], labels)
        self.assertEqual(len(alice_entry["competencies"]), 3)
        self.assertTrue(all(row["classification"] == "REQUIRED" for row in alice_entry["competencies"]))

    def test_build_full_comparison_skips_candidate_without_scored_evaluation_under_role(self):
        user = User.objects.create_user(
            email="skip-b2c@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        alice, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="s1", role_code="driver",
            dimension_percentages={"SAFETY": 90, "PRACTICAL_TASKS": 80, "BEHAVIORAL": 70, "COMMUNICATION": 60},
            not_applicable={"HYGIENE"},
        )
        unrelated = self._make_candidate(created_by=user, company=None, suffix="s2")

        entries = build_full_comparison(
            owner_type="USER", owner=user, role_code="driver",
            candidate_ids=[str(alice.public_id), str(unrelated.public_id)],
            language="en", actor=user,
        )

        self.assertEqual([e["candidate_id"] for e in entries], [str(alice.public_id)])

    # -- compute_key_differences --------------------------------------------

    def test_compute_key_differences_prioritizes_critical_and_reports_correct_best_worst(self):
        user = User.objects.create_user(
            email="diff-b2c@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        diff_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        alice, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="k1", role_code="driver",
            dimension_percentages={"SAFETY": 95, "PRACTICAL_TASKS": 60, "BEHAVIORAL": 55, "COMMUNICATION": 90},
            not_applicable={"HYGIENE"}, rule_set=diff_rule_set,
        )
        bob, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="k2", role_code="driver",
            dimension_percentages={"SAFETY": 40, "PRACTICAL_TASKS": 58, "BEHAVIORAL": 90, "COMMUNICATION": 50},
            not_applicable={"HYGIENE"}, rule_set=diff_rule_set,
        )
        entries = build_full_comparison(
            owner_type="USER", owner=user, role_code="driver",
            candidate_ids=[str(alice.public_id), str(bob.public_id)],
            language="en", actor=user,
        )

        differences = compute_key_differences(entries, language="en")

        # SAFETY (CRITICAL, spread 55) must outrank COMMUNICATION
        # (NON_CRITICAL, spread 40) even though both are large spreads.
        self.assertEqual(differences[0]["label"], DIMENSION_LABELS["SAFETY"])
        self.assertIn("95%", differences[0]["text"])
        self.assertIn("40%", differences[0]["text"])

    def test_compute_key_differences_returns_empty_for_single_candidate(self):
        self.assertEqual(compute_key_differences([{"candidate_name": "Solo", "competencies": []}]), [])

    # -- B2B/B2C endpoint integration ---------------------------------------

    def test_b2b_comparison_roles_endpoint_scopes_to_requesting_company_only(self):
        user_a = User.objects.create_user(
            email="b2b-roles-a@example.com", password="testpass123",
            first_name="A", last_name="Co", role=Roles.B2B, is_verified=True,
        )
        company_a = Company.objects.create(
            name="Company A", registration_number="ROLES-A", company_size="11-50",
            industry="Care", phone_number="+251900000001", country="Ethiopia",
            city="Addis Ababa", admin_user=user_a,
        )
        CompanyEmployerProfile.objects.create(
            user=user_a, company_name=company_a.name,
            company_registration_number=company_a.registration_number,
            company_size=company_a.company_size, company=company_a,
        )
        user_b = User.objects.create_user(
            email="b2b-roles-b@example.com", password="testpass123",
            first_name="B", last_name="Co", role=Roles.B2B, is_verified=True,
        )
        company_b = Company.objects.create(
            name="Company B", registration_number="ROLES-B", company_size="11-50",
            industry="Care", phone_number="+251900000002", country="Ethiopia",
            city="Addis Ababa", admin_user=user_b,
        )
        CompanyEmployerProfile.objects.create(
            user=user_b, company_name=company_b.name,
            company_registration_number=company_b.registration_number,
            company_size=company_b.company_size, company=company_b,
        )
        self._make_scored_candidate(
            created_by=user_a, company=company_a, suffix="ca1", role_code="driver",
            dimension_percentages={"SAFETY": 80}, not_applicable={"HYGIENE"},
        )

        self.client.force_authenticate(user_b)
        response = self.client.get("/api/v1/dashboard/b2b/candidate-comparison/roles")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_b2b_comparison_full_endpoint_rejects_out_of_range_candidate_count(self):
        user = User.objects.create_user(
            email="b2b-range@example.com", password="testpass123",
            first_name="B2B", last_name="User", role=Roles.B2B, is_verified=True,
        )
        company = Company.objects.create(
            name="Range Co", registration_number="RANGE-001", company_size="11-50",
            industry="Care", phone_number="+251900000003", country="Ethiopia",
            city="Addis Ababa", admin_user=user,
        )
        CompanyEmployerProfile.objects.create(
            user=user, company_name=company.name,
            company_registration_number=company.registration_number,
            company_size=company.company_size, company=company,
        )
        alice, _ = self._make_scored_candidate(
            created_by=user, company=company, suffix="rg1", role_code="driver",
            dimension_percentages={"SAFETY": 80}, not_applicable={"HYGIENE"},
        )
        self.client.force_authenticate(user)

        too_few = self.client.get(
            "/api/v1/dashboard/b2b/candidate-comparison/full",
            {"role_code": "driver", "candidate_ids": str(alice.public_id)},
        )
        self.assertEqual(too_few.status_code, 400)

        missing_role = self.client.get(
            "/api/v1/dashboard/b2b/candidate-comparison/full",
            {"candidate_ids": f"{alice.public_id},{alice.public_id}"},
        )
        self.assertEqual(missing_role.status_code, 400)

    def test_b2c_comparison_full_endpoint_returns_expected_shape(self):
        user = User.objects.create_user(
            email="b2c-full@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        full_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        alice, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="f1", role_code="driver",
            dimension_percentages={"SAFETY": 90, "PRACTICAL_TASKS": 80, "BEHAVIORAL": 70, "COMMUNICATION": 60},
            not_applicable={"HYGIENE"}, rule_set=full_rule_set,
        )
        bob, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="f2", role_code="driver",
            dimension_percentages={"SAFETY": 50, "PRACTICAL_TASKS": 60, "BEHAVIORAL": 85, "COMMUNICATION": 90},
            not_applicable={"HYGIENE"}, rule_set=full_rule_set,
        )
        self.client.force_authenticate(user)

        response = self.client.get(
            "/api/v1/dashboard/b2c/candidate-comparison/full",
            {"role_code": "driver", "candidate_ids": f"{alice.public_id},{bob.public_id}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role_code"], "driver")
        self.assertEqual(response.data["role_name"], "Driver")
        self.assertEqual(len(response.data["candidates"]), 2)
        self.assertTrue(len(response.data["key_differences"]) >= 1)

    def test_b2c_comparison_pdf_endpoint_returns_pdf(self):
        user = User.objects.create_user(
            email="b2c-pdf@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        pdf_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        alice, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="p1", role_code="driver",
            dimension_percentages={"SAFETY": 90, "PRACTICAL_TASKS": 80, "BEHAVIORAL": 70, "COMMUNICATION": 60},
            not_applicable={"HYGIENE"}, rule_set=pdf_rule_set,
        )
        bob, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="p2", role_code="driver",
            dimension_percentages={"SAFETY": 50, "PRACTICAL_TASKS": 60, "BEHAVIORAL": 85, "COMMUNICATION": 90},
            not_applicable={"HYGIENE"}, rule_set=pdf_rule_set,
        )
        self.client.force_authenticate(user)

        response = self.client.get(
            "/api/v1/dashboard/b2c/candidate-comparison/pdf",
            {"role_code": "driver", "candidate_ids": f"{alice.public_id},{bob.public_id}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_b2c_comparison_eligible_candidates_requires_role_code(self):
        user = User.objects.create_user(
            email="b2c-eligreq@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/dashboard/b2c/candidate-comparison/eligible-candidates")

        self.assertEqual(response.status_code, 400)

    # -- Admin / Superadmin endpoint integration -----------------------------

    def _make_admin_comparison_fixture(self):
        """One B2B company and one B2C user, each with two comparable
        driver candidates, plus an admin and a superadmin."""
        b2b_user = User.objects.create_user(
            email="adm-cmp-b2b@example.com", password="testpass123",
            first_name="B2B", last_name="Owner", role=Roles.B2B, is_verified=True,
        )
        company = Company.objects.create(
            name="Admin Compare Co", registration_number="ADM-CMP-1", company_size="11-50",
            industry="Care", phone_number="+251900000009", country="Ethiopia",
            city="Addis Ababa", admin_user=b2b_user,
        )
        b2c_user = User.objects.create_user(
            email="adm-cmp-b2c@example.com", password="testpass123",
            first_name="Solo", last_name="Sponsor", role=Roles.B2C, is_verified=True,
        )
        dims = {"SAFETY": 90, "PRACTICAL_TASKS": 80, "BEHAVIORAL": 70, "COMMUNICATION": 60}
        other_dims = {"SAFETY": 50, "PRACTICAL_TASKS": 60, "BEHAVIORAL": 85, "COMMUNICATION": 90}

        company_rules = self._make_rule_set(created_by=b2b_user, company=company, role_code="driver")
        co_a, _ = self._make_scored_candidate(
            created_by=b2b_user, company=company, suffix="adm1", role_code="driver",
            dimension_percentages=dims, not_applicable={"HYGIENE"}, rule_set=company_rules,
        )
        co_b, _ = self._make_scored_candidate(
            created_by=b2b_user, company=company, suffix="adm2", role_code="driver",
            dimension_percentages=other_dims, not_applicable={"HYGIENE"}, rule_set=company_rules,
        )
        user_rules = self._make_rule_set(created_by=b2c_user, company=None, role_code="driver")
        b2c_a, _ = self._make_scored_candidate(
            created_by=b2c_user, company=None, suffix="adm3", role_code="driver",
            dimension_percentages=dims, not_applicable={"HYGIENE"}, rule_set=user_rules,
        )
        b2c_b, _ = self._make_scored_candidate(
            created_by=b2c_user, company=None, suffix="adm4", role_code="driver",
            dimension_percentages=other_dims, not_applicable={"HYGIENE"}, rule_set=user_rules,
        )
        admin = User.objects.create_user(
            email="adm-cmp-admin@example.com", password="testpass123",
            first_name="Ad", last_name="Min", role=Roles.ADMIN, is_verified=True,
        )
        superadmin = User.objects.create_user(
            email="adm-cmp-super@example.com", password="testpass123",
            first_name="Super", last_name="Admin", role=Roles.SUPERADMIN, is_verified=True,
        )
        return {
            "company": company, "b2c_user": b2c_user, "b2b_user": b2b_user,
            "company_candidates": (co_a, co_b), "b2c_candidates": (b2c_a, b2c_b),
            "admin": admin, "superadmin": superadmin,
        }

    def test_admin_comparison_accounts_lists_companies_and_b2c_users_with_scored_candidates(self):
        f = self._make_admin_comparison_fixture()
        self.client.force_authenticate(f["admin"])

        response = self.client.get("/api/v1/dashboard/admin/candidate-comparison/accounts")

        self.assertEqual(response.status_code, 200)
        accounts = {(a["owner_type"], a["owner_id"]) for a in response.data}
        self.assertEqual(accounts, {
            ("COMPANY", str(f["company"].public_id)),
            ("USER", str(f["b2c_user"].public_id)),
        })
        self.assertEqual({a["candidate_count"] for a in response.data}, {2})

    def test_admin_comparison_accounts_skips_accounts_with_nothing_comparable(self):
        f = self._make_admin_comparison_fixture()
        stale_owner = User.objects.create_user(
            email="adm-cmp-stale@example.com", password="testpass123",
            first_name="Stale", last_name="Owner", role=Roles.B2C, is_verified=True,
        )
        retired_rules = ScoringRuleSet.objects.create(
            name="Retired driver rules", version="v0", role_code="driver", role_name="Driver",
            evaluation_tier=InterviewEvaluationTier.FULL, is_active=False, created_by=stale_owner,
        )
        self._make_scored_candidate(
            created_by=stale_owner, company=None, suffix="adm5", role_code="driver",
            dimension_percentages={"SAFETY": 70}, not_applicable={"HYGIENE"}, rule_set=retired_rules,
        )
        self.client.force_authenticate(f["admin"])

        response = self.client.get("/api/v1/dashboard/admin/candidate-comparison/accounts")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(str(stale_owner.public_id), {a["owner_id"] for a in response.data})

    def test_admin_comparison_flow_is_scoped_to_the_selected_account(self):
        f = self._make_admin_comparison_fixture()
        self.client.force_authenticate(f["superadmin"])
        company_scope = {"owner_type": "COMPANY", "owner_id": str(f["company"].public_id)}

        roles = self.client.get("/api/v1/dashboard/admin/candidate-comparison/roles", company_scope)
        self.assertEqual(roles.status_code, 200)
        self.assertEqual([(r["role_code"], r["candidate_count"]) for r in roles.data], [("driver", 2)])

        eligible = self.client.get(
            "/api/v1/dashboard/admin/candidate-comparison/eligible-candidates",
            {**company_scope, "role_code": "driver"},
        )
        self.assertEqual(eligible.status_code, 200)
        self.assertEqual(
            {c["candidate_id"] for c in eligible.data},
            {str(c.public_id) for c in f["company_candidates"]},
        )

        # A B2C user's candidate passed under the company scope is dropped.
        co_a, _ = f["company_candidates"]
        b2c_a, _ = f["b2c_candidates"]
        full = self.client.get(
            "/api/v1/dashboard/admin/candidate-comparison/full",
            {**company_scope, "role_code": "driver", "candidate_ids": f"{co_a.public_id},{b2c_a.public_id}"},
        )
        self.assertEqual(full.status_code, 200)
        self.assertEqual([e["candidate_id"] for e in full.data["candidates"]], [str(co_a.public_id)])

    def test_admin_comparison_full_and_pdf_for_b2c_account(self):
        f = self._make_admin_comparison_fixture()
        self.client.force_authenticate(f["admin"])
        a, b = f["b2c_candidates"]
        params = {
            "owner_type": "USER", "owner_id": str(f["b2c_user"].public_id),
            "role_code": "driver", "candidate_ids": f"{a.public_id},{b.public_id}",
        }

        full = self.client.get("/api/v1/dashboard/admin/candidate-comparison/full", params)
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.data["role_name"], "Driver")
        self.assertEqual(len(full.data["candidates"]), 2)
        self.assertTrue(len(full.data["key_differences"]) >= 1)

        pdf = self.client.get("/api/v1/dashboard/admin/candidate-comparison/pdf", params)
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

        from api.audit.models import AuditLog
        self.assertEqual(
            sorted(AuditLog.objects.filter(user=f["admin"]).values_list("action", flat=True)),
            ["EXPORT_CANDIDATE_COMPARISON", "VIEW_CANDIDATE_COMPARISON"],
        )

    def test_admin_comparison_rejects_missing_or_invalid_account(self):
        f = self._make_admin_comparison_fixture()
        self.client.force_authenticate(f["admin"])
        url = "/api/v1/dashboard/admin/candidate-comparison/roles"

        self.assertEqual(self.client.get(url).status_code, 400)
        self.assertEqual(self.client.get(url, {"owner_type": "COMPANY", "owner_id": "not-an-id"}).status_code, 404)
        # A B2B user is never a comparison owner on their own - only their company is.
        self.assertEqual(
            self.client.get(url, {"owner_type": "USER", "owner_id": str(f["b2b_user"].public_id)}).status_code,
            404,
        )

    def test_admin_comparison_endpoints_forbid_non_admins(self):
        f = self._make_admin_comparison_fixture()
        for user in (f["b2b_user"], f["b2c_user"]):
            self.client.force_authenticate(user)
            response = self.client.get("/api/v1/dashboard/admin/candidate-comparison/accounts")
            self.assertEqual(response.status_code, 403)

    # -- assessment-version compatibility (spec item 2) ----------------------
    # Nothing in the data model retires an older ScoringRuleSet once a newer
    # one is added for the same role (is_active is never auto-flipped), so
    # these lock down that Candidate Comparison itself refuses to mix a
    # candidate scored under a superseded rule set in with one scored under
    # the role's current rule set - matching exactly what a brand-new
    # evaluation would be scored against today (ScoringService._resolve_rule_set).

    def test_get_eligible_candidates_excludes_a_candidate_scored_under_a_superseded_rule_set(self):
        user = User.objects.create_user(
            email="version-eligible-b2c@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        old_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        stale, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="v1", role_code="driver",
            dimension_percentages={"SAFETY": 80}, not_applicable={"HYGIENE"}, rule_set=old_rule_set,
        )
        # A later rule-set change for the same role, created after `stale`
        # was already scored - exactly the real-world "rules updated since
        # this candidate's assessment" scenario.
        new_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        current, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="v2", role_code="driver",
            dimension_percentages={"SAFETY": 75}, not_applicable={"HYGIENE"}, rule_set=new_rule_set,
        )

        eligible = get_eligible_candidates(owner_type="USER", owner=user, role_code="driver")

        self.assertEqual([c["candidate_id"] for c in eligible], [str(current.public_id)])

    def test_get_comparable_roles_does_not_count_a_candidate_on_a_superseded_rule_set(self):
        user = User.objects.create_user(
            email="version-roles-b2c@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        old_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        self._make_scored_candidate(
            created_by=user, company=None, suffix="vr1", role_code="driver",
            dimension_percentages={"SAFETY": 80}, not_applicable={"HYGIENE"}, rule_set=old_rule_set,
        )
        new_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        self._make_scored_candidate(
            created_by=user, company=None, suffix="vr2", role_code="driver",
            dimension_percentages={"SAFETY": 75}, not_applicable={"HYGIENE"}, rule_set=new_rule_set,
        )

        roles = get_comparable_roles(owner_type="USER", owner=user)

        by_code = {r["role_code"]: r for r in roles}
        self.assertEqual(by_code["driver"]["candidate_count"], 1)

    def test_build_full_comparison_excludes_a_stale_rule_set_candidate_even_if_explicitly_requested(self):
        """Defense in depth: even if a caller passes the stale candidate's
        id directly (bypassing step 2's eligible-candidates list), the
        comparison itself must still never place them alongside a
        current-rule-set candidate."""
        user = User.objects.create_user(
            email="version-build-b2c@example.com", password="testpass123",
            first_name="B2C", last_name="User", role=Roles.B2C, is_verified=True,
        )
        old_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        stale, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="vb1", role_code="driver",
            dimension_percentages={"SAFETY": 80}, not_applicable={"HYGIENE"}, rule_set=old_rule_set,
        )
        new_rule_set = self._make_rule_set(created_by=user, company=None, role_code="driver")
        current, _ = self._make_scored_candidate(
            created_by=user, company=None, suffix="vb2", role_code="driver",
            dimension_percentages={"SAFETY": 75}, not_applicable={"HYGIENE"}, rule_set=new_rule_set,
        )

        entries = build_full_comparison(
            owner_type="USER", owner=user, role_code="driver",
            candidate_ids=[str(stale.public_id), str(current.public_id)],
            language="en", actor=user,
        )

        self.assertEqual([e["candidate_id"] for e in entries], [str(current.public_id)])


class DashboardParityChartsApiTests(TestCase):
    """B2B and B2C dashboards each grew a few charts the other side lacked
    (language distribution, evaluation trend, and monthly activity on B2B
    only; job-role distribution and evaluation time-of-day on B2C only).
    These cover the new endpoints added to close that gap, scoped
    correctly (a company's/user's own data only)."""

    def setUp(self):
        self.client = APIClient()

    def _make_candidate(self, *, owner, company=None, job_role="NA", language="EN", email):
        return Candidate.objects.create(
            first_name="Test",
            last_name="Candidate",
            email=email,
            passport_id=f"CMP-{email}",
            job_role=job_role,
            core_skills="care",
            preferred_language=language,
            passport_document="candidates/documents/passport/test.pdf",
            created_by=owner,
            company=company,
        )

    def _make_evaluation(self, *, candidate, owner, company=None, status="SCHEDULED",
                          scheduled_date=None, certificate_status="NOT_ISSUED"):
        # Evaluation.save() always overwrites candidate_preferred_language
        # (and job_role/company) from the linked Candidate on creation - set
        # those on the Candidate via _make_candidate(), not here.
        return Evaluation.objects.create(
            candidate=candidate,
            evaluation_type="INTERVIEW",
            status=status,
            scheduled_date=scheduled_date or timezone.now(),
            duration_minutes=45,
            candidate_first_name=candidate.first_name,
            candidate_last_name=candidate.last_name,
            candidate_email=candidate.email,
            candidate_passport_id=candidate.passport_id,
            candidate_job_role=candidate.job_role,
            candidate_preferred_language=candidate.preferred_language,
            certificate_status=certificate_status,
            created_by=owner,
            company=company,
        )

    def _make_b2c_user(self, email):
        return User.objects.create_user(
            email=email, password="testpass123", first_name="B2C", last_name="User",
            role=Roles.B2C, is_verified=True,
        )

    def _make_b2b_user_and_company(self, email, company_name, reg_number):
        user = User.objects.create_user(
            email=email, password="testpass123", first_name="B2B", last_name="User",
            role=Roles.B2B, is_verified=True,
        )
        company = Company.objects.create(
            name=company_name, registration_number=reg_number, company_size="11-50",
            industry="Care", phone_number="+251900000099", country="Ethiopia",
            city="Addis Ababa", admin_user=user,
        )
        CompanyEmployerProfile.objects.create(
            user=user, company_name=company.name,
            company_registration_number=company.registration_number,
            company_size=company.company_size, company=company,
        )
        return user, company

    def test_b2c_language_distribution_scopes_to_own_candidates(self):
        user = self._make_b2c_user("b2c-lang@example.com")
        other_user = self._make_b2c_user("b2c-lang-other@example.com")
        self.client.force_authenticate(user)

        c1 = self._make_candidate(owner=user, language="EN", email="lang1@example.com")
        c2 = self._make_candidate(owner=user, language="EN", email="lang2@example.com")
        c3 = self._make_candidate(owner=user, language="AR", email="lang3@example.com")
        self._make_evaluation(candidate=c1, owner=user)
        self._make_evaluation(candidate=c2, owner=user)
        self._make_evaluation(candidate=c3, owner=user)

        # Belongs to a different B2C user entirely - must not leak in.
        other_candidate = self._make_candidate(owner=other_user, language="AR", email="lang-other@example.com")
        self._make_evaluation(candidate=other_candidate, owner=other_user)

        response = self.client.get("/api/v1/dashboard/b2c/language-distribution")

        self.assertEqual(response.status_code, 200)
        by_lang = {item["language"]: item for item in response.data}
        self.assertEqual(by_lang["EN"]["count"], 2)
        self.assertEqual(by_lang["AR"]["count"], 1)
        self.assertEqual(by_lang["EN"]["percentage"], 66.67)

    def test_b2c_evaluation_trend_counts_by_status(self):
        user = self._make_b2c_user("b2c-trend@example.com")
        self.client.force_authenticate(user)

        c1 = self._make_candidate(owner=user, email="trend1@example.com")
        c2 = self._make_candidate(owner=user, email="trend2@example.com")
        c3 = self._make_candidate(owner=user, email="trend3@example.com")
        self._make_evaluation(candidate=c1, owner=user, status="SCHEDULED")
        self._make_evaluation(candidate=c2, owner=user, status="COMPLETED")
        self._make_evaluation(candidate=c3, owner=user, status="CANCELLED")

        response = self.client.get("/api/v1/dashboard/b2c/evaluation-trend")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        today = response.data[0]
        self.assertEqual(today["scheduled_count"], 1)
        self.assertEqual(today["completed_count"], 1)
        self.assertEqual(today["cancelled_count"], 1)

    def test_b2c_monthly_activity_counts_candidates_evaluations_and_certificates(self):
        user = self._make_b2c_user("b2c-monthly@example.com")
        self.client.force_authenticate(user)

        c1 = self._make_candidate(owner=user, email="monthly1@example.com")
        c2 = self._make_candidate(owner=user, email="monthly2@example.com")
        self._make_evaluation(
            candidate=c1, owner=user, status="COMPLETED", certificate_status="ISSUED",
        )

        response = self.client.get("/api/v1/dashboard/b2c/monthly-activity")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        month = response.data[0]
        self.assertEqual(month["candidates_added"], 2)
        self.assertEqual(month["evaluations_completed"], 1)
        self.assertEqual(month["certificates_issued"], 1)

    def test_b2b_job_role_distribution_scopes_to_company(self):
        user, company = self._make_b2b_user_and_company(
            "b2b-role@example.com", "Role Co", "ROLE-001",
        )
        other_user, other_company = self._make_b2b_user_and_company(
            "b2b-role-other@example.com", "Other Role Co", "ROLE-002",
        )
        self.client.force_authenticate(user)

        self._make_candidate(owner=user, company=company, job_role="NA", email="role1@example.com")
        self._make_candidate(owner=user, company=company, job_role="NA", email="role2@example.com")
        self._make_candidate(owner=user, company=company, job_role="DR", email="role3@example.com")
        # A different company's candidate - must not leak in.
        self._make_candidate(
            owner=other_user, company=other_company, job_role="DR", email="role-other@example.com",
        )

        response = self.client.get("/api/v1/dashboard/b2b/job-role-distribution")

        self.assertEqual(response.status_code, 200)
        by_role = {item["job_role"]: item for item in response.data}
        self.assertEqual(by_role["NA"]["count"], 2)
        self.assertEqual(by_role["DR"]["count"], 1)
        self.assertEqual(by_role["NA"]["percentage"], 66.67)

    def test_b2b_evaluation_time_range_buckets_by_hour_and_scopes_to_company(self):
        user, company = self._make_b2b_user_and_company(
            "b2b-time@example.com", "Time Co", "TIME-001",
        )
        other_user, other_company = self._make_b2b_user_and_company(
            "b2b-time-other@example.com", "Other Time Co", "TIME-002",
        )
        self.client.force_authenticate(user)

        morning_candidate = self._make_candidate(owner=user, company=company, email="time1@example.com")
        evening_candidate = self._make_candidate(owner=user, company=company, email="time2@example.com")
        tz = timezone.get_current_timezone()
        morning = timezone.now().replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=tz)
        evening = timezone.now().replace(hour=20, minute=0, second=0, microsecond=0, tzinfo=tz)
        self._make_evaluation(
            candidate=morning_candidate, owner=user, company=company, scheduled_date=morning,
        )
        self._make_evaluation(
            candidate=evening_candidate, owner=user, company=company, scheduled_date=evening,
        )

        # A different company's evaluation - must not leak in.
        other_candidate = self._make_candidate(
            owner=other_user, company=other_company, email="time-other@example.com",
        )
        self._make_evaluation(
            candidate=other_candidate, owner=other_user, company=other_company, scheduled_date=morning,
        )

        response = self.client.get("/api/v1/dashboard/b2b/evaluation-time-range")

        self.assertEqual(response.status_code, 200)
        by_range = {item["range"]: item["count"] for item in response.data}
        self.assertEqual(by_range["Morning (6am-12pm)"], 1)
        self.assertEqual(by_range["Evening (6pm-12am)"], 1)


class AdminRevenueAccuracyTests(TestCase):
    """Regression coverage for the admin dashboard's revenue/subscription
    stats - previously a subscription's real Stripe payment (recorded as a
    Payment row at signup) and its normalized monthly price (re-projected
    across every month it overlapped) were both added to the same total,
    double-counting real revenue; subscriptions whose Price had since been
    deleted (stripe_price -> NULL) were silently dropped from every count."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            email="admin@example.com",
            password="Password123!",
            first_name="Admin",
            last_name="User",
            role=Roles.ADMIN,
            is_verified=True,
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)

        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="Password123!",
            first_name="Company",
            last_name="Owner",
            role=Roles.B2B,
            is_verified=True,
        )
        self.customer = Customer.objects.create(
            user=self.owner, stripe_customer_id="cus_1", email=self.owner.email
        )
        self.price = Price.objects.create(
            name="Growth Package",
            stripe_price_id="price_growth",
            stripe_product_id="prod_growth",
            target_user_type="B2B",
            unit_amount=Decimal("2000.00"),
            currency="eur",
            interval="MONTHLY",
            interval_count=1,
            billing_type="RECURRING",
        )

    def test_revenue_trend_does_not_double_count_a_subscriptions_first_invoice(self):
        subscription = Subscription.objects.create(
            user=self.owner,
            customer=self.customer,
            stripe_price=self.price,
            stripe_subscription_id="sub_1",
            status="ACTIVE",
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
            quantity=1,
        )
        # The legacy signup-time record (still written by
        # StripeService.create_subscription) - must NOT be double-counted
        # against the Invoice below, which represents the same real charge.
        Payment.objects.create(
            user=self.owner,
            customer=self.customer,
            subscription=subscription,
            stripe_payment_intent_id="pi_1",
            amount=Decimal("2000.00"),
            currency="eur",
            status="SUCCEEDED",
        )
        Invoice.objects.create(
            user=self.owner,
            customer=self.customer,
            subscription=subscription,
            stripe_invoice_id="in_1",
            number="INV-001",
            status="PAID",
            amount_due=Decimal("2000.00"),
            amount_paid=Decimal("2000.00"),
            amount_remaining=Decimal("0.00"),
            currency="eur",
            paid_at=timezone.now(),
        )

        response = self.client.get("/api/v1/dashboard/admin/revenue-trend", {"months": 1})

        self.assertEqual(response.status_code, 200)
        current_month = response.data[-1]
        self.assertEqual(current_month["payment_revenue"], 0)
        self.assertEqual(current_month["subscription_revenue"], 2000.0)
        self.assertEqual(current_month["total_revenue"], 2000.0)

    def test_revenue_trend_includes_one_time_payments(self):
        Payment.objects.create(
            user=self.owner,
            customer=self.customer,
            subscription=None,
            stripe_payment_intent_id="pi_onetime",
            amount=Decimal("150.00"),
            currency="eur",
            status="SUCCEEDED",
        )

        response = self.client.get("/api/v1/dashboard/admin/revenue-trend", {"months": 1})

        current_month = response.data[-1]
        self.assertEqual(current_month["payment_revenue"], 150.0)
        self.assertEqual(current_month["subscription_revenue"], 0)

    def test_stats_counts_subscription_with_deleted_price_but_excludes_it_from_mrr(self):
        Subscription.objects.create(
            user=self.owner,
            customer=self.customer,
            stripe_price=None,  # simulates a Price that was later deleted
            stripe_subscription_id="sub_orphan",
            status="ACTIVE",
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
            quantity=1,
        )

        response = self.client.get("/api/v1/dashboard/admin/stats")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["active_subscriptions_count"], 1)
        self.assertEqual(response.data["monthly_recurring_revenue"], 0)

    def test_package_contribution_buckets_deleted_price_subscriptions_separately(self):
        Subscription.objects.create(
            user=self.owner,
            customer=self.customer,
            stripe_price=None,
            stripe_subscription_id="sub_orphan_2",
            status="ACTIVE",
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
            quantity=1,
        )

        response = self.client.get("/api/v1/dashboard/admin/package-contribution")

        self.assertEqual(response.status_code, 200)
        names = {item["package_name"] for item in response.data}
        self.assertIn("Unknown Package", names)
        unknown = next(item for item in response.data if item["package_name"] == "Unknown Package")
        self.assertEqual(unknown["subscriber_count"], 1)


class B2BDashboardCustomizationApiTests(TestCase):
    """Company-level dashboard layout, the real Readiness Index (by
    readiness outcome, not evaluation status), and the decision-oriented
    Requires Attention queue."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="dash-owner@example.com", password="testpass123", first_name="Dash", last_name="Owner",
            role=Roles.B2B, is_verified=True,
        )
        self.company = Company.objects.create(
            name="Dash Co", registration_number="DASH-001", company_size="11-50", industry="Care",
            phone_number="+251900000101", country="Ethiopia", city="Addis Ababa", admin_user=self.owner,
        )
        CompanyEmployerProfile.objects.create(
            user=self.owner, company_name=self.company.name,
            company_registration_number=self.company.registration_number,
            company_size=self.company.company_size, company=self.company,
        )
        self.config = InterviewConfiguration.objects.create(
            role_name="Housekeeper", role_code="domestic_worker", language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL, duration_minutes=45, total_questions=1,
            allow_retries=True, max_retries=1, rubric_version="v2.0", question_set_version="v1.2",
        )
        self._seq = 0

    def _make_team_member(self, permissions):
        member = User.objects.create_user(
            email=f"dash-member-{len(permissions)}@example.com", password="testpass123",
            first_name="Team", last_name="Member", role=Roles.B2B_TEAM_MEMBER, is_verified=True,
            company=self.company,
        )
        TeamMemberProfile.objects.create(
            user=member, company=self.company, job_title="Recruiter", phone_number="+251900000102",
            permissions=permissions,
        )
        return member

    def _make_evaluation(self, *, readiness="READY", status="COMPLETED", company=None, owner=None,
                         readiness_enabled=True, human_review=None):
        self._seq += 1
        company = company or self.company
        owner = owner or self.owner
        candidate = Candidate.objects.create(
            first_name=f"Cand{self._seq}", last_name="Test", email=f"dash-cand-{self._seq}@example.com",
            passport_id=f"DASH-{self._seq}", job_role="NA", core_skills="care", preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf", created_by=owner, company=company,
        )
        session = InterviewSession.objects.create(
            candidate=candidate, organization=company, config=self.config, role_name="Housekeeper",
            role_code="domestic_worker", ui_language="EN", candidate_language="EN", tts_language_code="en-US",
            stt_language_code="en-US", total_questions=1, evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0", question_set_version="v1.2", expires_at=InterviewSession.build_expiry(30),
            created_by=owner,
        )
        evaluation = Evaluation.objects.create(
            session=session, candidate=candidate, evaluation_type=EvaluationType.INTERVIEW, status=status,
            scheduled_date=timezone.now(), duration_minutes=45, created_by=owner, company=company,
            readiness_status=readiness, readiness_indicator_enabled=readiness_enabled, score=Decimal("80"),
        )
        if human_review is not None:
            EvaluationReport.objects.create(
                evaluation=evaluation, session=session, candidate=candidate,
                report_number=f"RPT-DASH-{self._seq}", report_status=EvaluationReport.STATUS_ACTIVE,
                readiness_status=readiness, requires_human_review=human_review,
            )
        return evaluation

    # -- layout -------------------------------------------------------------

    def test_layout_defaults_until_customized_then_persists_per_company(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/dashboard/b2b/dashboard-layout")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_default"])
        self.assertEqual(response.data["widgets"], list(DEFAULT_WIDGETS))
        self.assertTrue(response.data["can_edit"])

        response = self.client.put(
            "/api/v1/dashboard/b2b/dashboard-layout",
            {"widgets": ["readiness_index", "language_distribution"]}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["widgets"], ["readiness_index", "language_distribution"])
        self.company.refresh_from_db()
        self.assertEqual(self.company.dashboard_layout, {"widgets": ["readiness_index", "language_distribution"]})

        # Saved at company level: a full-access team member sees the same layout but can't change it.
        member = self._make_team_member(list(CompanyTeamPermissions.ALL))
        self.client.force_authenticate(member)
        response = self.client.get("/api/v1/dashboard/b2b/dashboard-layout")
        self.assertEqual(response.data["widgets"], ["readiness_index", "language_distribution"])
        self.assertFalse(response.data["can_edit"])
        response = self.client.put(
            "/api/v1/dashboard/b2b/dashboard-layout", {"widgets": []}, format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_layout_rejects_unknown_and_duplicate_widgets_and_supports_reset(self):
        self.client.force_authenticate(self.owner)
        url = "/api/v1/dashboard/b2b/dashboard-layout"
        self.assertEqual(self.client.put(url, {"widgets": ["nope"]}, format="json").status_code, 400)
        self.assertEqual(
            self.client.put(url, {"widgets": ["readiness_index", "readiness_index"]}, format="json").status_code, 400,
        )
        self.assertEqual(self.client.put(url, {"widgets": "readiness_index"}, format="json").status_code, 400)

        # An explicitly empty layout is a valid choice (everything hidden), distinct from "default".
        response = self.client.put(url, {"widgets": []}, format="json")
        self.assertEqual(response.data["widgets"], [])
        self.assertFalse(response.data["is_default"])

        response = self.client.put(url, {"reset": True}, format="json")
        self.assertTrue(response.data["is_default"])
        self.assertEqual(response.data["widgets"], list(DEFAULT_WIDGETS))

    def test_layout_drops_retired_widget_ids_instead_of_failing(self):
        self.company.dashboard_layout = {"widgets": ["retired_widget", "evaluation_trend"]}
        self.company.save()
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/dashboard/b2b/dashboard-layout")
        self.assertEqual(response.data["widgets"], ["evaluation_trend"])

    # -- readiness ----------------------------------------------------------

    def test_readiness_distribution_uses_readiness_outcome_and_corrections(self):
        self._make_evaluation(readiness="READY")
        self._make_evaluation(readiness="READY")
        self._make_evaluation(readiness="NOT_READY")
        corrected = self._make_evaluation(readiness="NOT_READY")
        EvaluationReadinessCorrection.objects.create(
            evaluation=corrected, original_readiness_status="NOT_READY",
            corrected_readiness_status="READY", reason="Reviewed", corrected_by=self.owner,
        )
        # Not counted: not completed, readiness disabled (e.g. Screening), other company.
        self._make_evaluation(readiness="PENDING", status="SCHEDULED")
        self._make_evaluation(readiness="READY", readiness_enabled=False)
        other_owner = User.objects.create_user(
            email="dash-other@example.com", password="testpass123", first_name="O", last_name="O",
            role=Roles.B2B, is_verified=True,
        )
        other_company = Company.objects.create(
            name="Other", registration_number="DASH-002", company_size="11-50", industry="Care",
            phone_number="+251900000103", country="Ethiopia", city="Addis Ababa", admin_user=other_owner,
        )
        self._make_evaluation(readiness="NOT_READY", company=other_company, owner=other_owner)

        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/dashboard/b2b/readiness-distribution")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 4)
        by_status = {row["status"]: row["count"] for row in response.data["distribution"]}
        self.assertEqual(by_status["READY"], 3)
        self.assertEqual(by_status["NOT_READY"], 1)
        self.assertEqual(response.data["ready_rate"], 75.0)

    def test_b2c_readiness_distribution_scoped_to_own_candidates(self):
        b2c_user = User.objects.create_user(
            email="dash-b2c@example.com", password="testpass123", first_name="B", last_name="C",
            role=Roles.B2C, is_verified=True,
        )
        self._make_evaluation(readiness="READY", company=None, owner=b2c_user)
        self._make_evaluation(readiness="PARTIALLY_READY", company=None, owner=b2c_user)
        self._make_evaluation(readiness="READY", status="SCHEDULED", company=None, owner=b2c_user)
        # The company's evaluations never count toward a B2C user's index.
        self._make_evaluation(readiness="NOT_READY")

        self.client.force_authenticate(b2c_user)
        response = self.client.get("/api/v1/dashboard/b2c/readiness-distribution")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 2)
        by_status = {row["status"]: row["count"] for row in response.data["distribution"]}
        self.assertEqual((by_status["READY"], by_status["PARTIALLY_READY"], by_status["NOT_READY"]), (1, 1, 0))
        self.assertEqual(response.data["ready_rate"], 50.0)

    def test_admin_readiness_distribution_is_platform_wide_and_admin_only(self):
        b2c_user = User.objects.create_user(
            email="dash-b2c-2@example.com", password="testpass123", first_name="B", last_name="C",
            role=Roles.B2C, is_verified=True,
        )
        self._make_evaluation(readiness="READY")
        self._make_evaluation(readiness="NOT_READY", company=None, owner=b2c_user)
        admin = User.objects.create_user(
            email="dash-admin@example.com", password="testpass123", first_name="A", last_name="D",
            role=Roles.ADMIN, is_verified=True,
        )

        self.client.force_authenticate(admin)
        response = self.client.get("/api/v1/dashboard/admin/readiness-distribution")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["ready_rate"], 50.0)

        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get("/api/v1/dashboard/admin/readiness-distribution").status_code, 403)

    def test_readiness_distribution_empty_company(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/dashboard/b2b/readiness-distribution")
        self.assertEqual(response.data["total"], 0)
        self.assertIsNone(response.data["ready_rate"])

    # -- requires attention -------------------------------------------------

    def test_requires_attention_lists_human_review_and_insufficient_evidence(self):
        review = self._make_evaluation(readiness="READY", human_review=True)
        incomplete = self._make_evaluation(readiness="INCOMPLETE")
        both = self._make_evaluation(readiness="INCOMPLETE", human_review=True)
        # Not flagged: clean report, report flagged but already corrected, still scheduled.
        self._make_evaluation(readiness="READY", human_review=False)
        resolved = self._make_evaluation(readiness="NOT_READY", human_review=True)
        EvaluationReadinessCorrection.objects.create(
            evaluation=resolved, original_readiness_status="NOT_READY",
            corrected_readiness_status="READY", reason="Reviewed", corrected_by=self.owner,
        )
        self._make_evaluation(readiness="INCOMPLETE", status="SCHEDULED")

        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/dashboard/b2b/requires-attention")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 3)
        self.assertEqual(response.data["human_review"], 2)
        self.assertEqual(response.data["insufficient_evidence"], 2)
        reasons = {item["evaluation_id"]: item["reasons"] for item in response.data["items"]}
        self.assertEqual(reasons[str(review.public_id)], ["HUMAN_REVIEW"])
        self.assertEqual(reasons[str(incomplete.public_id)], ["INSUFFICIENT_EVIDENCE"])
        self.assertEqual(reasons[str(both.public_id)], ["HUMAN_REVIEW", "INSUFFICIENT_EVIDENCE"])

    def test_requires_attention_respects_limit_but_counts_everything(self):
        for _ in range(3):
            self._make_evaluation(readiness="INCOMPLETE")
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/dashboard/b2b/requires-attention?limit=2")
        self.assertEqual(response.data["total"], 3)
        self.assertEqual(len(response.data["items"]), 2)

    def test_new_endpoints_require_full_team_access(self):
        member = self._make_team_member([CompanyTeamPermissions.ADD_CANDIDATES])
        self.client.force_authenticate(member)
        for url in (
            "/api/v1/dashboard/b2b/dashboard-layout",
            "/api/v1/dashboard/b2b/readiness-distribution",
            "/api/v1/dashboard/b2b/requires-attention",
        ):
            self.assertEqual(self.client.get(url).status_code, 403, url)
