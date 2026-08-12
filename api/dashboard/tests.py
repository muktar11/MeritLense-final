from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.candidates.models import Candidate
from api.core.constants import EvaluationType, InterviewEvaluationTier, Roles
from api.evaluations.models import Evaluation, ScoringRuleSet, SessionEvaluationSummary
from api.interviews.models import InterviewConfiguration
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
