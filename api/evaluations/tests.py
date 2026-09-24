from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.db import DatabaseError
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient
from urllib.parse import parse_qs, urlsplit

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.audit.models import AuditLog
from api.candidates.models import Candidate
from api.core.constants import AgreementMethod, AgreementStatus, AgreementType, AuditLogAction, CandidateJobRoles, CandidateResponseType, CoverageLevel, EvaluationLayer, EvaluationStatus, EvaluationType, InterviewEvaluationTier, QuestionDifficulty, QuestionLifecycleStatus, ReadinessStatus, Roles, SubscriptionStatus, BillingInterval
from api.contracts.models import Agreement
from api.evaluations.models import Certificate, CompetencyEvaluationResult, Evaluation, EvaluationReadinessDecisionRecord, EvaluatorRating, ResponseEvaluationResult, ScoringRule, ScoringRuleSet, SessionEvaluationSummary
from api.evaluations.scoring_services import Week6ScoringService
from api.evaluations.certificate_services import certificate_eligibility, generate_certificate
from api.payments.models import Customer, Price, Subscription, PackageBalance
from api.questions.models import QuestionTemplate
from api.scores.models import CandidateScore, ScoreSet
from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion
from api.interviews.models import InterviewConfiguration
from api.translation.models import EvaluationInputArtifact


def covered_competencies():
    return [
        {
            "competency_code": "safety_awareness",
            "competency_name": "Safety Awareness",
            "percentage": 82.0,
            "response_count": 1,
            "completed_response_count": 1,
            "status": "MEETS_THRESHOLD",
        },
        {
            "competency_code": "hygiene_standards",
            "competency_name": "Hygiene Standards",
            "percentage": 78.0,
            "response_count": 1,
            "completed_response_count": 1,
            "status": "MEETS_THRESHOLD",
        },
        {
            "competency_code": "communication_ability",
            "competency_name": "Communication Ability",
            "percentage": 80.0,
            "response_count": 1,
            "completed_response_count": 1,
            "status": "MEETS_THRESHOLD",
        },
        {
            "competency_code": "practical_task_execution",
            "competency_name": "Practical Task Execution",
            "percentage": 75.0,
            "response_count": 1,
            "completed_response_count": 1,
            "status": "MEETS_THRESHOLD",
        },
    ]


def add_minimal_response_evidence(*, evaluation, session, candidate, user):
    """Creates one real, minimal ResponseEvaluationResult (with a backing
    CandidateResponse/SessionQuestion/ScoringRule) so _build_response_evidence
    has something to return. Several lightweight test fixtures fabricate a
    SessionEvaluationSummary directly, without ever running real responses
    through Week6ScoringService - _derive_assessment_quality now requires
    real evidence (not just a claimed evaluated_response_count) before
    "Excellent"/"Good" is reachable, so those fixtures need at least one
    real response on record to stay eligible for a certificate."""
    template = QuestionTemplate.objects.create(
        role_name="Housekeeper", role_code="domestic_worker",
        question_code=f"EVIDENCE-{evaluation.pk}", question_version="1.0",
        question_status=QuestionLifecycleStatus.ACTIVE, domain="Safety & Hygiene",
        skill_tag="safety_awareness", skill="Safety Awareness", sequence_number=1,
        difficulty=QuestionDifficulty.MEDIUM, question_text="What do you do when you see a spill?",
        question_type="safety", question_format="SCENARIO", language="EN",
        scoring_type="0/3/5", difficulty_score=2, estimated_time_seconds=60,
        expected_answer_type="multi_step", evaluation_tier=InterviewEvaluationTier.FULL,
        rubric_version="v2.0", question_set_version="v1.2", critical_question=False, is_active=True,
    )
    session_question = SessionQuestion.objects.create(
        session=session, question_template=template, question_text=template.question_text,
        domain=template.domain, skill=template.skill_tag, difficulty=template.difficulty,
        question_order=1, status="ANSWERED", is_mandatory=True,
        asked_at=timezone.now(), answered_at=timezone.now(),
    )
    response = CandidateResponse.objects.create(
        session=session, question=session_question, response_type=CandidateResponseType.TEXT,
        transcript="I would identify the hazard and clean the spill.",
        text_response="I would identify the hazard and clean the spill.",
        interpretation_status="COMPLETED", processing_status="RULE_INPUT_PREPARED",
        stt_status="COMPLETED", stt_confidence="0.9500",
    )
    rule_set = ScoringRuleSet.objects.create(
        name=f"Evidence Rules {evaluation.pk}", version="v1", role_code="domestic_worker", role_name="Housekeeper",
        evaluation_tier=InterviewEvaluationTier.FULL, is_active=True, created_by=user,
    )
    rule = ScoringRule.objects.create(
        rule_set=rule_set, competency_code="safety_awareness", competency_name="Safety Awareness",
        question_template=template, question_code=template.question_code,
        expected_indicators=["identify hazard", "clean spill"], required_indicators=["identify hazard"],
        weighted_indicators={"identify hazard": "4", "clean spill": "3"},
        max_score="10.00", pass_threshold="7.00",
        scoring_method=ScoringRule.SCORING_METHOD_WEIGHTED_MATCH, is_active=True,
    )
    ResponseEvaluationResult.objects.create(
        evaluation=evaluation, session=session, candidate=candidate, response=response, question=session_question,
        rule_set=rule_set, rule=rule, competency_code="safety_awareness", competency_name="Safety Awareness",
        score=Decimal("7"), max_score=Decimal("10"), percentage=Decimal("70.00"),
        passed_required_indicators=True, critical_failure=False, requires_human_review=False,
        matched_indicators=["identify hazard", "clean spill"], observed_indicators=["identify hazard", "clean spill"],
        missing_indicators=[],
    )


class EvaluationRuleEngineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="eval-owner@example.com",
            password="testpass123",
            first_name="Eval",
            last_name="Owner",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Critical",
            last_name="Candidate",
            email="critical@example.com",
            passport_id="CRIT-001",
            job_role="NA",
            core_skills="safety",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=self.user,
        )
        self.config = InterviewConfiguration.objects.create(
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
        self.session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            translation_target="",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
        )
        self.template = QuestionTemplate.objects.create(
            role_name="Housekeeper",
            role_code="domestic_worker",
            question_code="HK-SAF-001",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Safety & Hygiene",
            skill_tag="Safety Awareness",
            skill="Safety Awareness",
            sequence_number=1,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text="Critical safety question",
            question_type="safety",
            question_format="SCENARIO",
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=2,
            estimated_time_seconds=60,
            expected_answer_type="multi_step",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            critical_question=True,
            is_active=True,
        )
        self.session_question = SessionQuestion.objects.create(
            session=self.session,
            question_template=self.template,
            question_text=self.template.question_text,
            domain=self.template.domain,
            skill=self.template.skill_tag,
            difficulty=self.template.difficulty,
            question_order=1,
            status="ANSWERED",
            is_mandatory=True,
            asked_at=timezone.now(),
            answered_at=timezone.now(),
        )
        self.evaluation = Evaluation.objects.create(
            session=self.session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=45,
            created_by=self.user,
        )

    def test_critical_zero_score_triggers_readiness_override(self):
        CandidateResponse.objects.create(
            session=self.session,
            question=self.session_question,
            response_type=CandidateResponseType.TEXT,
            transcript="Unsafe answer",
            text_response="Unsafe answer",
            metadata={"score": 0},
        )
        score_set = ScoreSet.objects.create(
            candidate=self.candidate,
            evaluation=self.evaluation,
            created_by=self.user,
            company=self.candidate.company,
        )
        CandidateScore.objects.create(
            candidate=self.candidate,
            evaluation=self.evaluation,
            area="COMMUNICATION",
            score=85,
            created_by=self.user,
            company=self.candidate.company,
        )

        score_set.calculate_average()
        self.evaluation.refresh_from_db()

        self.assertEqual(self.evaluation.readiness_status, ReadinessStatus.NOT_READY)
        self.assertTrue(self.evaluation.readiness_override_applied)
        self.assertIn("HK-SAF-001", self.evaluation.readiness_override_reason)
        record = EvaluationReadinessDecisionRecord.objects.get(evaluation=self.evaluation)
        self.assertEqual(record.readiness_indicator, "غير جاهز")
        self.assertTrue(record.override_triggered)
        self.assertEqual(record.session, self.session)
        self.assertIn("HK-SAF-001", record.readiness_reason)

    def test_non_zero_critical_score_does_not_trigger_override(self):
        CandidateResponse.objects.create(
            session=self.session,
            question=self.session_question,
            response_type=CandidateResponseType.TEXT,
            transcript="Safe answer",
            text_response="Safe answer",
            metadata={"score": 3},
        )
        score_set = ScoreSet.objects.create(
            candidate=self.candidate,
            evaluation=self.evaluation,
            created_by=self.user,
            company=self.candidate.company,
        )
        CandidateScore.objects.create(
            candidate=self.candidate,
            evaluation=self.evaluation,
            area="COMMUNICATION",
            score=85,
            created_by=self.user,
            company=self.candidate.company,
        )

        score_set.calculate_average()
        self.evaluation.refresh_from_db()

        self.assertEqual(self.evaluation.readiness_status, ReadinessStatus.PENDING)
        self.assertFalse(self.evaluation.readiness_override_applied)


@override_settings(
    FRONTEND_URL="https://frontend.example.com",
    INTERVIEW_FRONTEND_PATH_TEMPLATE="/{locale}/interview",
)
class EvaluationInterviewSchedulingApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="b2c-eval@example.com",
            password="testpass123",
            first_name="B2C",
            last_name="Evaluator",
            role=Roles.B2C,
            is_verified=True,
        )
        self.client.force_authenticate(self.user)
        customer = Customer.objects.create(
            user=self.user,
            stripe_customer_id="cus_eval_link_tests",
            email=self.user.email,
            name=self.user.get_full_name(),
        )
        price = Price.objects.create(
            name="B2C Test Plan",
            stripe_price_id="price_eval_link_tests",
            stripe_product_id="prod_eval_link_tests",
            target_user_type="B2C",
            unit_amount="99.00",
            currency="usd",
            interval=BillingInterval.MONTHLY,
            billing_type="RECURRING",
            feature_limits={"evaluation_limit": 10},
            is_active=True,
        )
        Subscription.objects.create(
            user=self.user,
            customer=customer,
            stripe_subscription_id="sub_eval_link_tests",
            stripe_price=price,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
        )
        PackageBalance.objects.create(
            owner_user=self.user, balance_type=PackageBalance.SLOTS, fixed_amount=1000, current_balance=1000,
        )
        self.candidate = Candidate.objects.create(
            first_name="Wondwosen",
            last_name="Beketu",
            email="candidate-link@example.com",
            passport_id="LINK-001",
            job_role="NA",
            core_skills="care,safety",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=self.user,
        )
        self.config = InterviewConfiguration.objects.create(
            role_name="Nanny",
            role_code="nanny",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30,
            total_questions=2,
            allow_retries=True,
            max_retries=1,
            rubric_version="v1",
            question_set_version="v1",
            is_active=True,
        )
        QuestionTemplate.objects.create(
            role_name="Nanny",
            role_code="nanny",
            question_code="NAN-LINK-001",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Safety",
            skill_tag="Safety",
            skill="Safety",
            sequence_number=1,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text="How do you keep a child safe?",
            question_type="knowledge",
            question_format="TEXT",
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=2,
            estimated_time_seconds=45,
            expected_answer_type="structured",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1",
            question_set_version="v1",
            is_active=True,
        )
        QuestionTemplate.objects.create(
            role_name="Nanny",
            role_code="nanny",
            question_code="NAN-LINK-002",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Care",
            skill_tag="Care",
            skill="Care",
            sequence_number=2,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text="How do you calm a child?",
            question_type="behavioral",
            question_format="TEXT",
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=2,
            estimated_time_seconds=45,
            expected_answer_type="structured",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1",
            question_set_version="v1",
            is_active=True,
        )

    def test_create_interview_evaluation_auto_generates_ai_interview_link(self):
        scheduled_date = timezone.now() + timezone.timedelta(days=1)
        response = self.client.post(
            "/api/v1/evaluations/evaluations",
            {
                "candidate": str(self.candidate.public_id),
                "evaluation_type": EvaluationType.INTERVIEW,
                "scheduled_date": scheduled_date.isoformat(),
                "duration_minutes": 60,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        evaluation = Evaluation.objects.get(public_id=response.data["id"])
        self.assertIsNotNone(evaluation.session_id)
        self.assertEqual(response.data["session_id"], str(evaluation.session.public_id))
        link = urlsplit(evaluation.meeting_link)
        query = parse_qs(link.query)
        self.assertEqual(link.path, "/en/interview")
        self.assertEqual(query["sessionId"], [str(evaluation.session.public_id)])
        self.assertEqual(query["token"], [evaluation.session.access_token])
        self.assertEqual(evaluation.meeting_id, str(evaluation.session.public_id))
        self.assertEqual(evaluation.session.scheduled_start_at.isoformat(), scheduled_date.isoformat())
        self.assertEqual(evaluation.scheduled_date.isoformat(), scheduled_date.isoformat())
        self.assertEqual(evaluation.duration_minutes, 60)

    def test_reschedule_and_cancel_interview_evaluation_sync_the_ai_session(self):
        scheduled_date = timezone.now() + timezone.timedelta(days=1)
        create_response = self.client.post(
            "/api/v1/evaluations/evaluations",
            {
                "candidate": str(self.candidate.public_id),
                "evaluation_type": EvaluationType.INTERVIEW,
                "scheduled_date": scheduled_date.isoformat(),
                "duration_minutes": 45,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        evaluation = Evaluation.objects.get(public_id=create_response.data["id"])

        new_date = timezone.now() + timezone.timedelta(days=2)
        reschedule_response = self.client.post(
            f"/api/v1/evaluations/evaluations/{evaluation.public_id}/reschedule",
            {"new_date": new_date.isoformat(), "reason": "Candidate requested new time"},
            format="json",
        )
        self.assertEqual(reschedule_response.status_code, 200, reschedule_response.data)
        evaluation.refresh_from_db()
        evaluation.session.refresh_from_db()
        self.assertEqual(evaluation.status, EvaluationStatus.RESCHEDULED)
        self.assertEqual(evaluation.scheduled_date.isoformat(), new_date.isoformat())
        self.assertEqual(evaluation.session.scheduled_start_at.isoformat(), new_date.isoformat())

        cancel_response = self.client.post(
            f"/api/v1/evaluations/evaluations/{evaluation.public_id}/cancel",
            {"reason": "Position closed"},
            format="json",
        )
        self.assertEqual(cancel_response.status_code, 200, cancel_response.data)
        evaluation.refresh_from_db()
        evaluation.session.refresh_from_db()
        self.assertEqual(evaluation.status, EvaluationStatus.CANCELLED)
        self.assertEqual(evaluation.cancellation_reason, "Position closed")
        self.assertEqual(evaluation.session.status, "CANCELLED")
        self.assertEqual(evaluation.session.cancellation_reason, "Position closed")

    def test_missing_config_error_names_candidate_role(self):
        self.config.delete()
        response = self.client.post(
            "/api/v1/evaluations/evaluations",
            {
                "candidate": str(self.candidate.public_id),
                "evaluation_type": EvaluationType.INTERVIEW,
                "scheduled_date": (timezone.now() + timezone.timedelta(days=1)).isoformat(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Nanny", str(response.data["candidate"]))

    def test_create_rejects_past_scheduled_date_at_api_layer(self):
        response = self.client.post(
            "/api/v1/evaluations/evaluations",
            {
                "candidate": str(self.candidate.public_id),
                "evaluation_type": EvaluationType.INTERVIEW,
                "scheduled_date": (timezone.now() - timezone.timedelta(minutes=1)).isoformat(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("scheduled_date", response.data)

    def test_in_progress_and_completed_interviews_cannot_be_rescheduled_or_cancelled(self):
        for evaluation_status, session_status in (
            (EvaluationStatus.IN_PROGRESS, "IN_PROGRESS"),
            (EvaluationStatus.COMPLETED, "COMPLETED"),
        ):
            with self.subTest(status=evaluation_status):
                evaluation = self._create_interview_evaluation()
                Evaluation.objects.filter(pk=evaluation.pk).update(status=evaluation_status)
                InterviewSession.objects.filter(pk=evaluation.session_id).update(status=session_status)

                reschedule = self.client.post(
                    f"/api/v1/evaluations/evaluations/{evaluation.public_id}/reschedule",
                    {"new_date": (timezone.now() + timezone.timedelta(days=2)).isoformat()},
                    format="json",
                )
                cancel = self.client.post(
                    f"/api/v1/evaluations/evaluations/{evaluation.public_id}/cancel",
                    {},
                    format="json",
                )

                self.assertEqual(reschedule.status_code, 400)
                self.assertEqual(cancel.status_code, 400)

    def test_double_cancel_is_rejected(self):
        evaluation = self._create_interview_evaluation()
        url = f"/api/v1/evaluations/evaluations/{evaluation.public_id}/cancel"
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 200)
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)

    def test_non_owner_cannot_reschedule_or_cancel(self):
        evaluation = self._create_interview_evaluation()
        other_user = User.objects.create_user(
            email="other-eval@example.com",
            password="testpass123",
            role=Roles.B2C,
            is_verified=True,
        )
        self.client.force_authenticate(other_user)
        base_url = f"/api/v1/evaluations/evaluations/{evaluation.public_id}"

        reschedule = self.client.post(
            f"{base_url}/reschedule",
            {"new_date": (timezone.now() + timezone.timedelta(days=2)).isoformat()},
            format="json",
        )
        cancel = self.client.post(f"{base_url}/cancel", {}, format="json")

        self.assertIn(reschedule.status_code, {403, 404})
        self.assertIn(cancel.status_code, {403, 404})

    def _create_interview_evaluation(self):
        response = self.client.post(
            "/api/v1/evaluations/evaluations",
            {
                "candidate": str(self.candidate.public_id),
                "evaluation_type": EvaluationType.INTERVIEW,
                "scheduled_date": (timezone.now() + timezone.timedelta(days=1)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return Evaluation.objects.get(public_id=response.data["id"])


class InterviewRoleMappingTests(TestCase):
    """Candidate roles resolve to InterviewConfiguration.role_name via
    CandidateJobRoles.INTERVIEW_ROLE_NAME_MAP, not by exact-string-matching
    the candidate's CHOICES label (which found zero configs for EC/KA/MW/OT
    in production - see api/core/constants.py)."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="role-mapping@example.com",
            password="testpass123",
            first_name="Role",
            last_name="Mapper",
            role=Roles.B2C,
            is_verified=True,
        )
        self.client.force_authenticate(self.user)
        customer = Customer.objects.create(
            user=self.user,
            stripe_customer_id="cus_role_mapping_tests",
            email=self.user.email,
            name=self.user.get_full_name(),
        )
        price = Price.objects.create(
            name="B2C Role Mapping Test Plan",
            stripe_price_id="price_role_mapping_tests",
            stripe_product_id="prod_role_mapping_tests",
            target_user_type="B2C",
            unit_amount="99.00",
            currency="usd",
            interval=BillingInterval.MONTHLY,
            billing_type="RECURRING",
            feature_limits={"evaluation_limit": 10},
            is_active=True,
        )
        Subscription.objects.create(
            user=self.user,
            customer=customer,
            stripe_subscription_id="sub_role_mapping_tests",
            stripe_price=price,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
        )
        PackageBalance.objects.create(
            owner_user=self.user, balance_type=PackageBalance.SLOTS, fixed_amount=1000, current_balance=1000,
        )

    def _create_candidate(self, job_role, passport_id):
        return Candidate.objects.create(
            first_name="Test",
            last_name="Candidate",
            email=f"candidate-{passport_id}@example.com",
            passport_id=passport_id,
            job_role=job_role,
            core_skills="care,safety",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=self.user,
        )

    def _schedule(self, candidate):
        return self.client.post(
            "/api/v1/evaluations/evaluations",
            {
                "candidate": str(candidate.public_id),
                "evaluation_type": EvaluationType.INTERVIEW,
                "scheduled_date": (timezone.now() + timezone.timedelta(days=1)).isoformat(),
            },
            format="json",
        )

    def test_elder_companion_resolves_to_elderly_caregiver_config(self):
        InterviewConfiguration.objects.create(
            role_name="Elderly Caregiver",
            role_code="elderly_caregiver",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30,
            total_questions=1,
            rubric_version="v1",
            question_set_version="v1",
            is_active=True,
        )
        candidate = self._create_candidate(CandidateJobRoles.ELDERCOMPANION, "EC-001")
        response = self._schedule(candidate)
        self.assertEqual(response.status_code, 201, response.data)

    def test_kitchen_assistant_resolves_to_restaurant_staff_config(self):
        InterviewConfiguration.objects.create(
            role_name="Restaurant Staff",
            role_code="restaurant_staff",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30,
            total_questions=1,
            rubric_version="v1",
            question_set_version="v1",
            is_active=True,
        )
        candidate = self._create_candidate(CandidateJobRoles.KITCHENASSISTANT, "KA-001")
        response = self._schedule(candidate)
        self.assertEqual(response.status_code, 201, response.data)

    def test_maintenance_worker_resolves_to_skilled_trades_config(self):
        InterviewConfiguration.objects.create(
            role_name="Skilled Trades & Maintenance",
            role_code="skilled_trades_maintenance",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30,
            total_questions=1,
            rubric_version="v1",
            question_set_version="v1",
            is_active=True,
        )
        candidate = self._create_candidate(CandidateJobRoles.MAINTAINANCEWORKER, "MW-001")
        response = self._schedule(candidate)
        self.assertEqual(response.status_code, 201, response.data)

    def test_other_role_is_blocked_with_clear_message_even_if_a_matching_config_exists(self):
        InterviewConfiguration.objects.create(
            role_name="Other",
            role_code="other",
            language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30,
            total_questions=1,
            rubric_version="v1",
            question_set_version="v1",
            is_active=True,
        )
        candidate = self._create_candidate(CandidateJobRoles.OTHER, "OT-001")
        response = self._schedule(candidate)
        self.assertEqual(response.status_code, 400)
        self.assertIn("isn't supported yet", str(response.data["candidate"]))


class Week6ScoringServiceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="week6@example.com",
            password="testpass123",
            first_name="Week",
            last_name="Six",
            role=Roles.B2C,
            is_verified=True,
        )
        self.client.force_authenticate(self.user)
        self.candidate = Candidate.objects.create(
            first_name="Score",
            last_name="Candidate",
            email="score@example.com",
            passport_id="SCORE-001",
            job_role="NA",
            core_skills="safety",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=self.user,
        )
        self.config = InterviewConfiguration.objects.create(
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
        self.session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
        )
        self.template = QuestionTemplate.objects.create(
            role_name="Housekeeper",
            role_code="domestic_worker",
            question_code="HK-SAF-002",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Safety & Hygiene",
            skill_tag="safety_awareness",
            skill="Safety Awareness",
            sequence_number=1,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text="What do you do when you see a spill?",
            question_type="safety",
            question_format="SCENARIO",
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=2,
            estimated_time_seconds=60,
            expected_answer_type="multi_step",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            critical_question=False,
            is_active=True,
        )
        self.session_question = SessionQuestion.objects.create(
            session=self.session,
            question_template=self.template,
            question_text=self.template.question_text,
            domain=self.template.domain,
            skill=self.template.skill_tag,
            difficulty=self.template.difficulty,
            question_order=1,
            status="ANSWERED",
            is_mandatory=True,
            asked_at=timezone.now(),
            answered_at=timezone.now(),
        )
        self.response = CandidateResponse.objects.create(
            session=self.session,
            question=self.session_question,
            response_type=CandidateResponseType.TEXT,
            transcript="I would identify the hazard and clean the spill.",
            text_response="I would identify the hazard and clean the spill.",
            interpretation_status="COMPLETED",
            processing_status="RULE_INPUT_PREPARED",
        )
        EvaluationInputArtifact.objects.create(
            response=self.response,
            session=self.session,
            question=self.session_question,
            competency_code="safety_awareness",
            expected_indicators=["identify hazard", "clean spill", "prevent recurrence"],
            observed_indicators=["identify hazard", "clean spill"],
            missing_indicators=["prevent recurrence"],
            risk_flags=[],
            source_interpretation_status="COMPLETED",
            requires_human_review=False,
            metadata={"source": "week5"},
        )
        self.evaluation = Evaluation.objects.create(
            session=self.session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=45,
            created_by=self.user,
        )
        self.rule_set = ScoringRuleSet.objects.create(
            name="Week 6 Default",
            version="week6-v1",
            role_code="domestic_worker",
            role_name="Housekeeper",
            evaluation_tier=InterviewEvaluationTier.FULL,
            is_active=True,
            created_by=self.user,
            company=self.candidate.company,
        )
        ScoringRule.objects.create(
            rule_set=self.rule_set,
            competency_code="safety_awareness",
            competency_name="Safety Awareness",
            question_template=self.template,
            question_code="HK-SAF-002",
            expected_indicators=["identify hazard", "clean spill", "prevent recurrence"],
            required_indicators=["identify hazard"],
            weighted_indicators={
                "identify hazard": "4",
                "clean spill": "3",
                "prevent recurrence": "3",
            },
            max_score="10.00",
            pass_threshold="7.00",
            scoring_method=ScoringRule.SCORING_METHOD_WEIGHTED_MATCH,
            is_active=True,
        )

    def test_week6_scoring_service_generates_response_and_session_outputs(self):
        summary = Week6ScoringService.run_for_evaluation(
            evaluation=self.evaluation,
            actor=self.user,
            rule_set=self.rule_set,
        )

        response_result = ResponseEvaluationResult.objects.get(evaluation=self.evaluation, response=self.response)
        self.evaluation.refresh_from_db()

        self.assertEqual(float(response_result.score), 7.0)
        self.assertEqual(float(response_result.percentage), 70.0)
        self.assertEqual(response_result.missing_indicators, ["prevent recurrence"])
        self.assertFalse(response_result.critical_failure)
        self.assertEqual(summary.status, SessionEvaluationSummary.STATUS_EVALUATED)
        self.assertEqual(float(summary.overall_percentage), 70.0)
        # Only one of the five canonical competencies (Safety) has any
        # evidence in this fixture - clean scoring with no critical
        # failures is necessary but not sufficient for READY, so this
        # correctly lands on INCOMPLETE rather than a premature READY (see
        # Week6ScoringService._apply_evaluation_rollups' coverage check).
        self.assertEqual(self.evaluation.readiness_status, ReadinessStatus.INCOMPLETE)
        self.assertEqual(float(self.evaluation.score), 70.0)
        record = EvaluationReadinessDecisionRecord.objects.get(evaluation=self.evaluation)
        self.assertEqual(record.readiness_indicator, "أدلة غير كافية")
        self.assertFalse(record.override_triggered)
        self.assertEqual(record.rule_engine_version, "v1.0")
        self.assertEqual(record.session, self.session)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLogAction.RULE_ENGINE_DECISION_RECORDED,
                resource_id=self.evaluation.id,
            ).exists()
        )

    def test_rescoring_after_a_retag_drops_the_stale_competency_row(self):
        """A question's skill_tag can be retagged onto a different
        competency between two scoring runs of the same evaluation (e.g.
        a taxonomy migration). _aggregate_competencies must not leave the
        OLD competency_code behind as an orphaned CompetencyEvaluationResult
        row once no response maps to it anymore - it would still surface on
        a regenerated report/certificate alongside the correct new row."""
        Week6ScoringService.run_for_evaluation(evaluation=self.evaluation, actor=self.user, rule_set=self.rule_set)
        self.assertTrue(
            CompetencyEvaluationResult.objects.filter(
                evaluation=self.evaluation, rule_set=self.rule_set, competency_code="safety_awareness"
            ).exists()
        )

        self.template.skill_tag = "hygiene_standards"
        self.template.skill = "Hygiene & Standards"
        self.template.skill_id = "hygiene_standards"
        self.template.save(update_fields=["skill_tag", "skill", "skill_id"])
        rule = ScoringRule.objects.get(question_template=self.template)
        rule.competency_code = "hygiene_standards"
        rule.competency_name = "Hygiene & Standards"
        rule.save(update_fields=["competency_code", "competency_name"])
        artifact = EvaluationInputArtifact.objects.get(response=self.response)
        artifact.competency_code = "hygiene_standards"
        artifact.save(update_fields=["competency_code"])

        Week6ScoringService.run_for_evaluation(evaluation=self.evaluation, actor=self.user, rule_set=self.rule_set)

        self.assertFalse(
            CompetencyEvaluationResult.objects.filter(
                evaluation=self.evaluation, rule_set=self.rule_set, competency_code="safety_awareness"
            ).exists()
        )
        self.assertTrue(
            CompetencyEvaluationResult.objects.filter(
                evaluation=self.evaluation, rule_set=self.rule_set, competency_code="hygiene_standards"
            ).exists()
        )

    def test_week6_scoring_normalizes_legacy_competency_codes(self):
        self.rule_set.rules.all().delete()
        legacy_rule = ScoringRule.objects.create(
            rule_set=self.rule_set,
            competency_code="patient_safety",
            competency_name="Patient Safety",
            expected_indicators=["identify hazard", "clean spill", "prevent recurrence"],
            required_indicators=["identify hazard"],
            weighted_indicators={
                "identify hazard": "4",
                "clean spill": "3",
                "prevent recurrence": "3",
            },
            max_score="10.00",
            pass_threshold="7.00",
            scoring_method=ScoringRule.SCORING_METHOD_WEIGHTED_MATCH,
            is_active=True,
        )
        artifact = self.response.evaluation_input_artifact
        artifact.competency_code = "patient_safety"
        artifact.save(update_fields=["competency_code", "updated_at"])

        summary = Week6ScoringService.run_for_evaluation(
            evaluation=self.evaluation,
            actor=self.user,
            rule_set=self.rule_set,
        )

        response_result = ResponseEvaluationResult.objects.get(evaluation=self.evaluation, response=self.response)
        competency_result = CompetencyEvaluationResult.objects.get(
            evaluation=self.evaluation,
            competency_code="safety_awareness",
        )
        self.assertEqual(response_result.rule_id, legacy_rule.id)
        self.assertEqual(response_result.competency_code, "safety_awareness")
        self.assertEqual(response_result.competency_name, "Safety Awareness")
        self.assertEqual(competency_result.competency_code, "safety_awareness")
        self.assertEqual(competency_result.competency_name, "Safety Awareness")

    def test_unanswered_response_is_skipped_as_incomplete_instead_of_blocking_scoring(self):
        unmapped_template = QuestionTemplate.objects.create(
            role_name="Housekeeper",
            role_code="domestic_worker",
            question_code="HK-SAF-003",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Communication",
            skill_tag="communication",
            skill="Communication",
            sequence_number=2,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text="How do you greet the family each morning?",
            question_type="behavioral",
            question_format="SCENARIO",
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=1,
            estimated_time_seconds=30,
            expected_answer_type="multi_step",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            critical_question=False,
            is_active=True,
        )
        unanswered_question = SessionQuestion.objects.create(
            session=self.session,
            question_template=unmapped_template,
            question_text=unmapped_template.question_text,
            domain=unmapped_template.domain,
            skill=unmapped_template.skill_tag,
            difficulty=unmapped_template.difficulty,
            question_order=2,
            status="PENDING",
            is_mandatory=True,
        )
        unanswered_response = CandidateResponse.objects.create(
            session=self.session,
            question=unanswered_question,
            response_type=CandidateResponseType.VOICE,
        )

        summary = Week6ScoringService.run_for_evaluation(
            evaluation=self.evaluation,
            actor=self.user,
            rule_set=self.rule_set,
        )

        self.assertFalse(
            ResponseEvaluationResult.objects.filter(response=unanswered_response).exists()
        )
        self.assertEqual(summary.evaluated_response_count, 1)
        self.assertEqual(summary.total_response_count, 2)
        self.assertEqual(summary.incomplete_response_count, 1)
        self.assertEqual(summary.status, SessionEvaluationSummary.STATUS_PARTIALLY_EVALUATED)

    def test_critical_failure_preserves_raw_score_and_sets_flag(self):
        rule = self.rule_set.rules.get(question_code="HK-SAF-002")
        rule.critical_failure_indicators = ["clean spill"]
        rule.save(update_fields=["critical_failure_indicators", "updated_at"])

        summary = Week6ScoringService.run_for_evaluation(
            evaluation=self.evaluation,
            actor=self.user,
            rule_set=self.rule_set,
        )

        response_result = ResponseEvaluationResult.objects.get(evaluation=self.evaluation, response=self.response)
        self.evaluation.refresh_from_db()

        self.assertTrue(response_result.critical_failure)
        self.assertEqual(float(response_result.score), 7.0)
        self.assertEqual(response_result.metadata["raw_score"], "7.00")
        self.assertEqual(response_result.metadata["effective_score"], "0.00")
        self.assertEqual(self.evaluation.readiness_status, ReadinessStatus.NOT_READY)
        self.assertTrue(self.evaluation.readiness_override_applied)
        self.assertEqual(summary.critical_failures[0]["score"], 7.0)

    def test_readiness_legal_record_is_immutable_after_generation(self):
        Week6ScoringService.run_for_evaluation(
            evaluation=self.evaluation,
            actor=self.user,
            rule_set=self.rule_set,
        )
        record = EvaluationReadinessDecisionRecord.objects.get(evaluation=self.evaluation)
        record.readiness_reason = "changed later"

        with self.assertRaises(ValidationError):
            record.save()

        with self.assertRaises(DatabaseError):
            EvaluationReadinessDecisionRecord.objects.filter(pk=record.pk).update(
                readiness_reason="changed via queryset"
            )

        with self.assertRaises(DatabaseError):
            EvaluationReadinessDecisionRecord.objects.filter(pk=record.pk).delete()

    def test_run_scoring_endpoint_returns_frontend_ready_summary(self):
        response = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/run-scoring",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], SessionEvaluationSummary.STATUS_EVALUATED)
        self.assertEqual(response.data["overall_percentage"], "70.00")

        session_response = self.client.get(f"/api/v1/interviews/{self.session.public_id}/scoring-summary/")
        self.assertEqual(session_response.status_code, 200)
        self.assertEqual(session_response.data["status"], SessionEvaluationSummary.STATUS_EVALUATED)

        legal_record_response = self.client.get(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/readiness-legal-record"
        )
        self.assertEqual(legal_record_response.status_code, 200)
        # Only one of five canonical competencies (Safety) has evidence in
        # this fixture - see test_week6_scoring_service_generates_response_
        # and_session_outputs above for the same coverage-gate reasoning.
        self.assertEqual(legal_record_response.data["readiness_indicator"], "أدلة غير كافية")

    def test_screening_evaluation_skips_readiness_override(self):
        self.session.evaluation_tier = InterviewEvaluationTier.SCREENING
        self.session.coverage_level = CoverageLevel.SCREENING
        self.session.readiness_indicator_enabled = False
        self.session.save(update_fields=["evaluation_tier", "coverage_level", "readiness_indicator_enabled", "updated_at"])
        self.evaluation.evaluation_tier = InterviewEvaluationTier.SCREENING
        self.evaluation.coverage_level = CoverageLevel.SCREENING
        self.evaluation.readiness_indicator_enabled = False
        self.evaluation.save(update_fields=["evaluation_tier", "coverage_level", "readiness_indicator_enabled", "updated_at"])

        CandidateResponse.objects.create(
            session=self.session,
            question=self.session_question,
            response_type=CandidateResponseType.TEXT,
            transcript="Unsafe answer",
            text_response="Unsafe answer",
            metadata={"score": 0},
        )
        score_set = ScoreSet.objects.create(
            candidate=self.candidate,
            evaluation=self.evaluation,
            created_by=self.user,
            company=self.candidate.company,
        )
        CandidateScore.objects.create(
            candidate=self.candidate,
            evaluation=self.evaluation,
            area="COMMUNICATION",
            score=85,
            created_by=self.user,
            company=self.candidate.company,
        )

        score_set.calculate_average()
        self.evaluation.refresh_from_db()

        self.assertEqual(self.evaluation.readiness_status, ReadinessStatus.PENDING)
        self.assertFalse(self.evaluation.readiness_override_applied)


class ScoringRuleSetTenantScopingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="b2b-owner@example.com",
            password="testpass123",
            first_name="Owner",
            last_name="One",
            role=Roles.B2B,
            is_verified=True,
        )
        self.other_owner = User.objects.create_user(
            email="b2b-other@example.com",
            password="testpass123",
            first_name="Owner",
            last_name="Two",
            role=Roles.B2B,
            is_verified=True,
        )
        self.company = Company.objects.create(
            name="Alpha Care",
            registration_number="ALPHA-001",
            company_size="11-50",
            industry="Care",
            phone_number="+251900000001",
            country="Ethiopia",
            city="Addis Ababa",
            admin_user=self.owner,
            registration_certificate="companies/certificates/alpha.pdf",
        )
        self.other_company = Company.objects.create(
            name="Beta Care",
            registration_number="BETA-001",
            company_size="11-50",
            industry="Care",
            phone_number="+251900000002",
            country="Ethiopia",
            city="Addis Ababa",
            admin_user=self.other_owner,
            registration_certificate="companies/certificates/beta.pdf",
        )
        CompanyEmployerProfile.objects.create(
            user=self.owner,
            company_name=self.company.name,
            company_registration_number=self.company.registration_number,
            company_size=self.company.company_size,
            company=self.company,
        )
        CompanyEmployerProfile.objects.create(
            user=self.other_owner,
            company_name=self.other_company.name,
            company_registration_number=self.other_company.registration_number,
            company_size=self.other_company.company_size,
            company=self.other_company,
        )
        self.rule_set = ScoringRuleSet.objects.create(
            name="Tenant Scoped Rules",
            version="v1",
            role_code="nanny",
            role_name="Nanny",
            evaluation_tier=InterviewEvaluationTier.FULL,
            is_active=True,
            created_by=self.owner,
            company=self.company,
        )

    def test_b2b_user_only_sees_own_company_rule_sets(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/evaluations/rule-sets")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], str(self.rule_set.public_id))

    def test_b2b_user_cannot_retrieve_other_company_rule_set(self):
        self.client.force_authenticate(self.other_owner)
        response = self.client.get(f"/api/v1/evaluations/rule-sets/{self.rule_set.public_id}")

        self.assertEqual(response.status_code, 404)

    def test_b2b_rule_set_creation_is_automatically_company_scoped(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/evaluations/rule-sets",
            {
                "name": "New Company Rules",
                "version": "v2",
                "role_code": "nanny",
                "role_name": "Nanny",
                "evaluation_tier": InterviewEvaluationTier.FULL,
                "is_active": True,
                "rules": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        created = ScoringRuleSet.objects.get(public_id=response.data["id"])
        self.assertEqual(created.company, self.company)


class CandidateScoreSummaryApiTests(TestCase):
    """GET /evaluations/candidate-scores - reads real Week6ScoringService
    output (SessionEvaluationSummary.competencies_summary) directly,
    replacing the old ScoreSet/CandidateScore models that nothing in the
    actual scoring pipeline ever populated."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="scores-owner@example.com",
            password="testpass123",
            first_name="Scores",
            last_name="Owner",
            role=Roles.B2C,
            is_verified=True,
        )
        self.other_user = User.objects.create_user(
            email="scores-other@example.com",
            password="testpass123",
            first_name="Other",
            last_name="User",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Score",
            last_name="Candidate",
            email="score-summary@example.com",
            passport_id="SCORE-SUMMARY-001",
            job_role="NA",
            core_skills="safety",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=self.user,
        )
        self.config = InterviewConfiguration.objects.create(
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
        self.session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
        )
        self.template = QuestionTemplate.objects.create(
            role_name="Housekeeper",
            role_code="domestic_worker",
            question_code="HK-SAF-SUMMARY",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Safety & Hygiene",
            skill_tag="safety_awareness",
            skill="Safety Awareness",
            sequence_number=1,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text="What do you do when you see a spill?",
            question_type="safety",
            question_format="SCENARIO",
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=2,
            estimated_time_seconds=60,
            expected_answer_type="multi_step",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            critical_question=False,
            is_active=True,
        )
        self.session_question = SessionQuestion.objects.create(
            session=self.session,
            question_template=self.template,
            question_text=self.template.question_text,
            domain=self.template.domain,
            skill=self.template.skill_tag,
            difficulty=self.template.difficulty,
            question_order=1,
            status="ANSWERED",
            is_mandatory=True,
            asked_at=timezone.now(),
            answered_at=timezone.now(),
        )
        self.response = CandidateResponse.objects.create(
            session=self.session,
            question=self.session_question,
            response_type=CandidateResponseType.TEXT,
            transcript="I would identify the hazard and clean the spill.",
            text_response="I would identify the hazard and clean the spill.",
            interpretation_status="COMPLETED",
            processing_status="RULE_INPUT_PREPARED",
        )
        EvaluationInputArtifact.objects.create(
            response=self.response,
            session=self.session,
            question=self.session_question,
            competency_code="safety_awareness",
            expected_indicators=["identify hazard", "clean spill", "prevent recurrence"],
            observed_indicators=["identify hazard", "clean spill"],
            missing_indicators=["prevent recurrence"],
            risk_flags=[],
            source_interpretation_status="COMPLETED",
            requires_human_review=False,
            metadata={"source": "test"},
        )
        self.evaluation = Evaluation.objects.create(
            session=self.session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=45,
            created_by=self.user,
        )
        self.rule_set = ScoringRuleSet.objects.create(
            name="Score Summary Rules",
            version="v1",
            role_code="domestic_worker",
            role_name="Housekeeper",
            evaluation_tier=InterviewEvaluationTier.FULL,
            is_active=True,
            created_by=self.user,
            company=self.candidate.company,
        )
        ScoringRule.objects.create(
            rule_set=self.rule_set,
            competency_code="safety_awareness",
            competency_name="Safety Awareness",
            question_template=self.template,
            question_code="HK-SAF-SUMMARY",
            expected_indicators=["identify hazard", "clean spill", "prevent recurrence"],
            required_indicators=["identify hazard"],
            weighted_indicators={
                "identify hazard": "4",
                "clean spill": "3",
                "prevent recurrence": "3",
            },
            max_score="10.00",
            pass_threshold="7.00",
            scoring_method=ScoringRule.SCORING_METHOD_WEIGHTED_MATCH,
            is_active=True,
        )
        Week6ScoringService.run_for_evaluation(
            evaluation=self.evaluation,
            actor=self.user,
            rule_set=self.rule_set,
        )

    def test_owner_sees_candidate_with_real_competency_breakdown(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/evaluations/candidate-scores")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry["candidate_id"], str(self.candidate.public_id))
        self.assertEqual(entry["role_code"], "domestic_worker")
        self.assertEqual(float(entry["overall_percentage"]), 70.0)
        self.assertEqual(len(entry["competencies"]), 1)
        self.assertEqual(entry["competencies"][0]["code"], "safety_awareness")
        self.assertEqual(entry["competencies"][0]["name"], "Safety Awareness")
        self.assertEqual(float(entry["competencies"][0]["percentage"]), 70.0)
        # No Certificate row exists for this evaluation - the response says
        # so honestly rather than omitting the key or fabricating a link.
        self.assertIsNone(entry["certificate"])

    def test_certificate_is_included_once_generated(self):
        from api.evaluations.certificate_services import generate_certificate

        consent_agreement = Agreement.objects.create(
            user=self.user,
            agreement_type=AgreementType.CANDIDATE_CONSENT,
            version="v1",
            method=AgreementMethod.CHECKBOX,
            status=AgreementStatus.SIGNED,
            accepted_at=timezone.now(),
        )
        self.session.identity_verified = True
        self.session.status = "COMPLETED"
        self.session.ended_at = timezone.now()
        self.session.candidate_consent_agreement = consent_agreement
        self.session.save(update_fields=["identity_verified", "status", "ended_at", "candidate_consent_agreement"])
        self.evaluation.status = EvaluationStatus.COMPLETED
        self.evaluation.completed_at = timezone.now()
        self.evaluation.save(update_fields=["status", "completed_at"])
        summary = SessionEvaluationSummary.objects.get(evaluation=self.evaluation)
        summary.competencies_summary = covered_competencies()
        summary.save(update_fields=["competencies_summary"])
        generate_certificate(self.evaluation, summary)

        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/evaluations/candidate-scores")

        self.assertEqual(response.status_code, 200, response.data)
        entry = response.data[0]
        self.assertIsNotNone(entry["certificate"])
        self.assertEqual(entry["certificate"]["certificate_id"], self.evaluation.certificate.certificate_id)
        self.assertIn(".pdf", entry["certificate"]["pdf_url"])

    def test_returns_every_scored_evaluation_not_just_the_latest(self):
        # A candidate re-assessed later (e.g. for a retry, or a different
        # role) previously had their earlier score silently dropped by a
        # latest-only collapse - both must now come back, newest first.
        earlier_session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
        )
        earlier_evaluation = Evaluation.objects.create(
            session=earlier_session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() - timezone.timedelta(days=10),
            duration_minutes=45,
            created_by=self.user,
        )
        SessionEvaluationSummary.objects.create(
            evaluation=earlier_evaluation,
            session=earlier_session,
            candidate=self.candidate,
            rule_set=self.rule_set,
            total_score="6.00",
            max_score="10.00",
            overall_percentage="60.00",
            evaluated_response_count=1,
            total_response_count=1,
            status=SessionEvaluationSummary.STATUS_EVALUATED,
            generated_at=timezone.now() - timezone.timedelta(days=10),
        )

        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/evaluations/candidate-scores")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 2)
        candidate_id = str(self.candidate.public_id)
        self.assertTrue(all(entry["candidate_id"] == candidate_id for entry in response.data))
        # Newest (the setUp evaluation, run through the real scoring
        # pipeline) must come before the older, manually-inserted one.
        self.assertGreater(response.data[0]["generated_at"], response.data[1]["generated_at"])

    def test_other_user_does_not_see_this_candidate(self):
        self.client.force_authenticate(self.other_user)
        response = self.client.get("/api/v1/evaluations/candidate-scores")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, [])

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get("/api/v1/evaluations/candidate-scores")
        self.assertEqual(response.status_code, 401)


class LayerBreakdownCalculationTests(TestCase):
    """Unit tests against Week6ScoringService._compute_layer_breakdown
    directly (unsaved CompetencyEvaluationResult instances - the formula
    only reads evaluation_layer/total_score/max_score off them), rather
    than reverse-engineering indicator weights through the full response
    pipeline to hit exact target percentages."""

    def test_weighted_final_score_across_all_three_layers(self):
        results = [
            CompetencyEvaluationResult(
                evaluation_layer=EvaluationLayer.COGNITIVE, total_score=Decimal("8"), max_score=Decimal("10")
            ),
            CompetencyEvaluationResult(
                evaluation_layer=EvaluationLayer.BEHAVIORAL, total_score=Decimal("6"), max_score=Decimal("10")
            ),
            CompetencyEvaluationResult(
                evaluation_layer=EvaluationLayer.TASK_EXECUTION, total_score=Decimal("10"), max_score=Decimal("10")
            ),
        ]
        breakdown, final = Week6ScoringService._compute_layer_breakdown(results)

        self.assertEqual(breakdown[EvaluationLayer.COGNITIVE], {"percentage": 80.0, "weight": 50})
        self.assertEqual(breakdown[EvaluationLayer.BEHAVIORAL], {"percentage": 60.0, "weight": 30})
        self.assertEqual(breakdown[EvaluationLayer.TASK_EXECUTION], {"percentage": 100.0, "weight": 20})
        # 0.5*80 + 0.3*60 + 0.2*100 = 40 + 18 + 20 = 78.0
        self.assertEqual(float(final), 78.0)

    def test_missing_layer_weight_redistributes_among_present_layers(self):
        results = [
            CompetencyEvaluationResult(
                evaluation_layer=EvaluationLayer.COGNITIVE, total_score=Decimal("10"), max_score=Decimal("10")
            ),
            CompetencyEvaluationResult(
                evaluation_layer=EvaluationLayer.BEHAVIORAL, total_score=Decimal("5"), max_score=Decimal("10")
            ),
            # No Task Execution competency at all in this rule set.
        ]
        breakdown, final = Week6ScoringService._compute_layer_breakdown(results)

        self.assertNotIn(EvaluationLayer.TASK_EXECUTION, breakdown)
        # weight_sum = 50+30 = 80; (100*50 + 50*30) / 80 = 6500/80 = 81.25
        self.assertEqual(float(final), 81.25)

    def test_uncategorized_competencies_are_excluded_and_return_none(self):
        results = [
            CompetencyEvaluationResult(evaluation_layer="", total_score=Decimal("5"), max_score=Decimal("10")),
        ]
        breakdown, final = Week6ScoringService._compute_layer_breakdown(results)

        self.assertEqual(breakdown, {})
        self.assertIsNone(final)

    def test_mix_of_categorized_and_uncategorized_ignores_uncategorized(self):
        results = [
            CompetencyEvaluationResult(
                evaluation_layer=EvaluationLayer.COGNITIVE, total_score=Decimal("10"), max_score=Decimal("10")
            ),
            CompetencyEvaluationResult(evaluation_layer="", total_score=Decimal("0"), max_score=Decimal("10")),
        ]
        breakdown, final = Week6ScoringService._compute_layer_breakdown(results)

        self.assertEqual(list(breakdown.keys()), [EvaluationLayer.COGNITIVE])
        self.assertEqual(float(final), 100.0)


class AutomaticScoringOnCompletionTests(TestCase):
    """complete_session() (api/sessions/services.py) now calls
    Week6ScoringService.run_for_evaluation automatically instead of
    requiring a separate manual run-scoring call."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="auto-score@example.com",
            password="testpass123",
            first_name="Auto",
            last_name="Score",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Auto",
            last_name="Candidate",
            email="auto-score-candidate@example.com",
            passport_id="AUTO-SCORE-001",
            job_role="NA",
            core_skills="safety",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=self.user,
        )
        self.config = InterviewConfiguration.objects.create(
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
        self.session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
        )

    def test_completion_without_any_matching_rule_set_still_completes(self):
        from api.sessions.services import InterviewSessionService

        # No ScoringRuleSet exists for "domestic_worker" - this is the
        # normal state for 6 of 7 roles today. Completion must still
        # succeed, just unscored, exactly like before auto-scoring existed.
        InterviewSessionService.complete_session(self.session, actor=self.user)

        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "COMPLETED")
        self.assertFalse(SessionEvaluationSummary.objects.filter(session=self.session).exists())

    def test_completion_with_a_matching_rule_set_scores_automatically(self):
        from api.sessions.services import InterviewSessionService

        template = QuestionTemplate.objects.create(
            role_name="Housekeeper",
            role_code="domestic_worker",
            question_code="HK-AUTO-001",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Safety & Hygiene",
            skill_tag="safety_awareness",
            skill="Safety Awareness",
            sequence_number=1,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text="What do you do when you see a spill?",
            question_type="safety",
            question_format="SCENARIO",
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=2,
            estimated_time_seconds=60,
            expected_answer_type="multi_step",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            critical_question=False,
            is_active=True,
        )
        session_question = SessionQuestion.objects.create(
            session=self.session,
            question_template=template,
            question_text=template.question_text,
            domain=template.domain,
            skill=template.skill_tag,
            difficulty=template.difficulty,
            question_order=1,
            status="ANSWERED",
            is_mandatory=True,
            asked_at=timezone.now(),
            answered_at=timezone.now(),
        )
        response = CandidateResponse.objects.create(
            session=self.session,
            question=session_question,
            response_type=CandidateResponseType.TEXT,
            transcript="I would identify the hazard and clean the spill.",
            text_response="I would identify the hazard and clean the spill.",
            interpretation_status="COMPLETED",
            processing_status="RULE_INPUT_PREPARED",
        )
        EvaluationInputArtifact.objects.create(
            response=response,
            session=self.session,
            question=session_question,
            competency_code="safety_awareness",
            expected_indicators=["identify hazard"],
            observed_indicators=["identify hazard"],
            missing_indicators=[],
            risk_flags=[],
            source_interpretation_status="COMPLETED",
            requires_human_review=False,
            metadata={"source": "test"},
        )
        rule_set = ScoringRuleSet.objects.create(
            name="Auto Score Rules",
            version="v1",
            role_code="domestic_worker",
            role_name="Housekeeper",
            evaluation_tier=InterviewEvaluationTier.FULL,
            is_active=True,
            created_by=self.user,
            company=self.candidate.company,
        )
        ScoringRule.objects.create(
            rule_set=rule_set,
            competency_code="safety_awareness",
            competency_name="Safety Awareness",
            question_template=template,
            question_code="HK-AUTO-001",
            expected_indicators=["identify hazard"],
            required_indicators=["identify hazard"],
            weighted_indicators={"identify hazard": "10"},
            max_score="10.00",
            pass_threshold="7.00",
            scoring_method=ScoringRule.SCORING_METHOD_WEIGHTED_MATCH,
            is_active=True,
        )

        InterviewSessionService.complete_session(self.session, actor=self.user)

        summary = SessionEvaluationSummary.objects.get(session=self.session)
        self.assertEqual(float(summary.overall_percentage), 100.0)

    def test_completion_does_not_issue_a_certificate_when_coverage_is_insufficient(self):
        from api.sessions.services import InterviewSessionService

        template = QuestionTemplate.objects.create(
            role_name="Housekeeper",
            role_code="domestic_worker",
            question_code="HK-CERT-001",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Safety & Hygiene",
            skill_tag="safety_awareness",
            skill="Safety Awareness",
            sequence_number=1,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text="What do you do when you see a spill?",
            question_type="safety",
            question_format="SCENARIO",
            language="EN",
            scoring_type="0/3/5",
            difficulty_score=2,
            estimated_time_seconds=60,
            expected_answer_type="multi_step",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            critical_question=False,
            is_active=True,
        )
        session_question = SessionQuestion.objects.create(
            session=self.session,
            question_template=template,
            question_text=template.question_text,
            domain=template.domain,
            skill=template.skill_tag,
            difficulty=template.difficulty,
            question_order=1,
            status="ANSWERED",
            is_mandatory=True,
            asked_at=timezone.now(),
            answered_at=timezone.now(),
        )
        response = CandidateResponse.objects.create(
            session=self.session,
            question=session_question,
            response_type=CandidateResponseType.TEXT,
            transcript="I would identify the hazard and clean the spill.",
            text_response="I would identify the hazard and clean the spill.",
            interpretation_status="COMPLETED",
            processing_status="RULE_INPUT_PREPARED",
            stt_confidence=Decimal("0.92"),
        )
        EvaluationInputArtifact.objects.create(
            response=response,
            session=self.session,
            question=session_question,
            competency_code="safety_awareness",
            expected_indicators=["identify hazard"],
            observed_indicators=["identify hazard"],
            missing_indicators=[],
            risk_flags=[],
            source_interpretation_status="COMPLETED",
            requires_human_review=False,
            metadata={"source": "test"},
        )
        rule_set = ScoringRuleSet.objects.create(
            name="Cert Test Rules",
            version="v1",
            role_code="domestic_worker",
            role_name="Housekeeper",
            evaluation_tier=InterviewEvaluationTier.FULL,
            is_active=True,
            created_by=self.user,
            company=self.candidate.company,
        )
        ScoringRule.objects.create(
            rule_set=rule_set,
            competency_code="safety_awareness",
            competency_name="Safety Awareness",
            evaluation_layer=EvaluationLayer.COGNITIVE,
            question_template=template,
            question_code="HK-CERT-001",
            expected_indicators=["identify hazard"],
            required_indicators=["identify hazard"],
            weighted_indicators={"identify hazard": "10"},
            max_score="10.00",
            pass_threshold="7.00",
            scoring_method=ScoringRule.SCORING_METHOD_WEIGHTED_MATCH,
            is_active=True,
        )

        self.session.identity_verified = True
        self.session.save(update_fields=["identity_verified"])

        InterviewSessionService.complete_session(self.session, actor=self.user)

        evaluation = Evaluation.objects.get(session=self.session)
        self.assertTrue(evaluation.certificate_enabled)
        self.assertEqual(evaluation.certificate_status, "NOT_ISSUED")
        self.assertFalse(Certificate.objects.filter(evaluation=evaluation).exists())
        # The certificate gate and the readiness_status the report/PDF
        # displays must never disagree about whether coverage was
        # sufficient - only 1 of 5 canonical dimensions (Safety) has any
        # evidence here, so this must land on INCOMPLETE, not a false READY
        # (the exact contradiction this fix closes - see
        # Week6ScoringService._apply_evaluation_rollups).
        self.assertEqual(evaluation.readiness_status, ReadinessStatus.INCOMPLETE)

    def test_completion_with_sufficient_coverage_still_reaches_ready(self):
        from api.sessions.services import InterviewSessionService

        competencies = [
            ("SUF-SAF-001", "safety_awareness", "Safety Awareness"),
            ("SUF-HYG-001", "hygiene_standards", "Hygiene & Standards"),
            ("SUF-COM-001", "communication_ability", "Communication Ability"),
            ("SUF-TSK-001", "task_execution", "Practical Task Execution"),
        ]
        rule_set = ScoringRuleSet.objects.create(
            name="Sufficient Coverage Rules",
            version="v1",
            role_code="domestic_worker",
            role_name="Housekeeper",
            evaluation_tier=InterviewEvaluationTier.FULL,
            is_active=True,
            created_by=self.user,
            company=self.candidate.company,
        )
        for order, (question_code, competency_code, competency_name) in enumerate(competencies, start=1):
            template = QuestionTemplate.objects.create(
                role_name="Housekeeper",
                role_code="domestic_worker",
                question_code=question_code,
                question_version="1.0",
                question_status=QuestionLifecycleStatus.ACTIVE,
                domain=competency_name,
                skill_tag=competency_code,
                skill=competency_name,
                sequence_number=order,
                difficulty=QuestionDifficulty.MEDIUM,
                question_text=f"Demonstrate {competency_name}.",
                question_type="general",
                question_format="SCENARIO",
                language="EN",
                scoring_type="0/3/5",
                difficulty_score=2,
                estimated_time_seconds=60,
                expected_answer_type="multi_step",
                evaluation_tier=InterviewEvaluationTier.FULL,
                rubric_version="v2.0",
                question_set_version="v1.2",
                critical_question=False,
                is_active=True,
            )
            session_question = SessionQuestion.objects.create(
                session=self.session,
                question_template=template,
                question_text=template.question_text,
                domain=template.domain,
                skill=template.skill_tag,
                difficulty=template.difficulty,
                question_order=order,
                status="ANSWERED",
                is_mandatory=True,
                asked_at=timezone.now(),
                answered_at=timezone.now(),
            )
            response = CandidateResponse.objects.create(
                session=self.session,
                question=session_question,
                response_type=CandidateResponseType.TEXT,
                transcript="A complete, correct answer.",
                text_response="A complete, correct answer.",
                interpretation_status="COMPLETED",
                processing_status="RULE_INPUT_PREPARED",
                stt_confidence=Decimal("0.95"),
            )
            EvaluationInputArtifact.objects.create(
                response=response,
                session=self.session,
                question=session_question,
                competency_code=competency_code,
                expected_indicators=["do the thing"],
                observed_indicators=["do the thing"],
                missing_indicators=[],
                risk_flags=[],
                source_interpretation_status="COMPLETED",
                requires_human_review=False,
                metadata={"source": "test"},
            )
            ScoringRule.objects.create(
                rule_set=rule_set,
                competency_code=competency_code,
                competency_name=competency_name,
                question_template=template,
                question_code=question_code,
                expected_indicators=["do the thing"],
                required_indicators=["do the thing"],
                weighted_indicators={"do the thing": "10"},
                max_score="10.00",
                pass_threshold="7.00",
                scoring_method=ScoringRule.SCORING_METHOD_WEIGHTED_MATCH,
                is_active=True,
            )

        self.session.identity_verified = True
        self.session.total_questions = len(competencies)
        self.session.save(update_fields=["identity_verified", "total_questions"])

        InterviewSessionService.complete_session(self.session, actor=self.user)

        evaluation = Evaluation.objects.get(session=self.session)
        self.assertEqual(evaluation.readiness_status, ReadinessStatus.READY)


class CertificateGenerationTests(TestCase):
    """generate_certificate() directly, isolated from the completion flow -
    checks that every field either reflects real scoring data or is
    honestly None/N/A, matching Certificate's own docstring contract."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="cert-gen@example.com",
            password="testpass123",
            first_name="Cert",
            last_name="Gen",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Cert",
            last_name="Candidate",
            email="cert-gen-candidate@example.com",
            passport_id="CERTGEN0001",
            job_role="NA",
            core_skills="safety",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=self.user,
        )
        self.config = InterviewConfiguration.objects.create(
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
        self.session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
        )
        self.evaluation = Evaluation.objects.create(
            session=self.session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=45,
            created_by=self.user,
        )
        consent_agreement = Agreement.objects.create(
            user=self.user,
            agreement_type=AgreementType.CANDIDATE_CONSENT,
            version="v1",
            method=AgreementMethod.CHECKBOX,
            status=AgreementStatus.SIGNED,
            accepted_at=timezone.now(),
        )
        self.session.candidate_consent_agreement = consent_agreement
        self.session.save(update_fields=["candidate_consent_agreement"])

    def test_generate_certificate_uses_real_data(self):
        import base64
        from django.core.files.uploadedfile import SimpleUploadedFile
        tiny_jpeg = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
            "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
            "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEB"
            "AxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAA"
            "AAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k="
        )
        self.candidate.profile_photo = SimpleUploadedFile("photo.jpg", tiny_jpeg, content_type="image/jpeg")
        self.candidate.save()
        from api.core.constants import ReadinessStatus
        self.evaluation.readiness_status = ReadinessStatus.READY
        self.evaluation.status = EvaluationStatus.COMPLETED
        self.evaluation.completed_at = timezone.now()
        self.evaluation.save(update_fields=["readiness_status", "status", "completed_at"])
        self.session.identity_verified = True
        self.session.status = "COMPLETED"
        self.session.ended_at = timezone.now()
        self.session.save(update_fields=["identity_verified", "status", "ended_at"])
        summary = SessionEvaluationSummary.objects.create(
            evaluation=self.evaluation,
            session=self.session,
            candidate=self.candidate,
            rule_set=ScoringRuleSet.objects.create(
                name="Cert Gen Rules", version="v1", role_code="domestic_worker", role_name="Housekeeper",
                evaluation_tier=InterviewEvaluationTier.FULL, is_active=True, created_by=self.user,
            ),
            total_score=Decimal("83"),
            max_score=Decimal("100"),
            overall_percentage=Decimal("83.00"),
            layer_breakdown={
                "COGNITIVE": {"percentage": 87.0, "weight": 50},
                "BEHAVIORAL": {"percentage": 82.0, "weight": 30},
                "TASK_EXECUTION": {"percentage": 90.0, "weight": 20},
            },
            competencies_summary=[
                *covered_competencies(),
            ],
            total_response_count=1,
            evaluated_response_count=1,
            status=SessionEvaluationSummary.STATUS_EVALUATED,
        )
        add_minimal_response_evidence(
            evaluation=self.evaluation, session=self.session, candidate=self.candidate, user=self.user,
        )

        certificate = generate_certificate(self.evaluation, summary)
        self.assertIsNotNone(certificate)

        self.assertTrue(certificate.certificate_id.startswith(f"ML-{timezone.now().year}-"))
        # Two independent identifiers on two independent counters - the
        # certificate document vs. the underlying assessment record - never
        # the same value or derived from one another.
        self.assertTrue(certificate.assessment_id.startswith(f"ASM-{timezone.now().year}-"))
        self.assertNotEqual(certificate.certificate_id, certificate.assessment_id)
        self.assertTrue(certificate.pdf_file.name)
        pdf_bytes = certificate.pdf_file.read()
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIsNone(certificate.expires_at)

        from api.evaluations.certificate_services import _candidate_photo_context, _readiness_gauge_context
        photo_data_uri, photo_verified = _candidate_photo_context(self.candidate)
        self.assertTrue(photo_data_uri.startswith("data:image/jpeg;base64,"))
        self.assertFalse(photo_verified)
        readiness = _readiness_gauge_context(self.evaluation, "en")
        self.assertEqual(readiness["label"], "Ready")
        self.assertEqual(readiness["position"], 3)

        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, "ISSUED")
        self.assertIsNotNone(self.evaluation.certificate_issued_at)

    def test_candidate_photo_is_none_when_never_uploaded(self):
        from api.evaluations.certificate_services import _candidate_photo_context

        photo_data_uri, photo_verified = _candidate_photo_context(self.candidate)
        self.assertIsNone(photo_data_uri)
        self.assertFalse(photo_verified)

    def test_candidate_photo_prefers_verification_photo_and_marks_verified(self):
        # Regression guard: the "Verified ID Photo" badge must describe the
        # same image actually shown, not a different field's presence -
        # see _candidate_photo_context's docstring.
        import base64
        from django.core.files.uploadedfile import SimpleUploadedFile
        from api.evaluations.certificate_services import _candidate_photo_context

        tiny_jpeg = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
            "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
            "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEB"
            "AxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAA"
            "AAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k="
        )
        self.candidate.verification_photo = SimpleUploadedFile(
            "verify.jpg", tiny_jpeg, content_type="image/jpeg"
        )
        self.candidate.save(update_fields=["verification_photo"])

        photo_data_uri, photo_verified = _candidate_photo_context(self.candidate)
        self.assertTrue(photo_data_uri.startswith("data:image/jpeg;base64,"))
        self.assertTrue(photo_verified)

    def test_readiness_gauge_reflects_real_evaluation_status(self):
        from api.core.constants import ReadinessStatus
        from api.evaluations.certificate_services import _readiness_gauge_context

        self.evaluation.readiness_status = ReadinessStatus.NOT_READY
        self.evaluation.save(update_fields=["readiness_status"])
        not_ready = _readiness_gauge_context(self.evaluation, "en")
        self.assertEqual(not_ready, {"label": "Readiness Gaps Identified", "position": 1})

        # PENDING (the default before scoring rolls it up to READY/NOT_READY)
        # maps to the rule engine's own PARTIALLY_READY/"Partially Ready"
        # middle ground - not a guess, the same mapping EvaluationReportService
        # uses for the internal report.
        self.evaluation.readiness_status = ReadinessStatus.PENDING
        self.evaluation.save(update_fields=["readiness_status"])
        pending = _readiness_gauge_context(self.evaluation, "en")
        self.assertEqual(pending, {"label": "Partially Ready", "position": 2})

    def test_regenerating_keeps_the_same_certificate_id(self):
        self.session.identity_verified = True
        self.session.status = "COMPLETED"
        self.session.ended_at = timezone.now()
        self.session.save(update_fields=["identity_verified", "status", "ended_at"])
        self.evaluation.status = EvaluationStatus.COMPLETED
        self.evaluation.completed_at = timezone.now()
        self.evaluation.save(update_fields=["status", "completed_at"])
        summary = SessionEvaluationSummary.objects.create(
            evaluation=self.evaluation,
            session=self.session,
            candidate=self.candidate,
            rule_set=ScoringRuleSet.objects.create(
                name="Cert Regen Rules", version="v1", role_code="domestic_worker", role_name="Housekeeper",
                evaluation_tier=InterviewEvaluationTier.FULL, is_active=True, created_by=self.user,
            ),
            total_score=Decimal("70"), max_score=Decimal("100"), overall_percentage=Decimal("70.00"),
            competencies_summary=covered_competencies(), status=SessionEvaluationSummary.STATUS_EVALUATED,
            total_response_count=1, evaluated_response_count=1,
        )
        add_minimal_response_evidence(
            evaluation=self.evaluation, session=self.session, candidate=self.candidate, user=self.user,
        )

        first = generate_certificate(self.evaluation, summary)
        second = generate_certificate(self.evaluation, summary)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.certificate_id, second.certificate_id)
        self.assertEqual(first.assessment_id, second.assessment_id)
        self.assertEqual(first.issued_at, second.issued_at)


class CertificateArabicLanguageTests(TestCase):
    """A candidate who interviewed in Arabic (InterviewSession.candidate_language)
    gets an Arabic certificate - real RTL template, translated labels, a
    /ar/ verification URL - not the English one with the language silently
    ignored."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="cert-ar@example.com", password="testpass123", first_name="Cert", last_name="Arabic",
            role=Roles.B2C, is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Fatima", last_name="AlRashid", email="cert-ar-candidate@example.com",
            passport_id="CERTAR0001", job_role="NA", core_skills="safety", preferred_language="AR",
            passport_document="candidates/documents/passport/test.pdf", created_by=self.user,
        )
        self.config = InterviewConfiguration.objects.create(
            role_name="Housekeeper", role_code="domestic_worker", language="AR",
            evaluation_tier=InterviewEvaluationTier.FULL, duration_minutes=45, total_questions=1,
            allow_retries=True, max_retries=1, rubric_version="v2.0", question_set_version="v1.2",
        )
        self.session = InterviewSession.objects.create(
            candidate=self.candidate, organization=self.candidate.company, config=self.config,
            role_name=self.config.role_name, role_code=self.config.role_code,
            ui_language="AR", candidate_language="AR", tts_language_code="ar-SA", stt_language_code="ar-SA",
            total_questions=1, evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0", question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30), created_by=self.user,
        )
        self.evaluation = Evaluation.objects.create(
            session=self.session, candidate=self.candidate, evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1), duration_minutes=45, created_by=self.user,
        )
        consent_agreement = Agreement.objects.create(
            user=self.user, agreement_type=AgreementType.CANDIDATE_CONSENT, version="v1",
            method=AgreementMethod.CHECKBOX, status=AgreementStatus.SIGNED, accepted_at=timezone.now(),
        )
        self.session.candidate_consent_agreement = consent_agreement
        self.session.save(update_fields=["candidate_consent_agreement"])

        from api.core.constants import ReadinessStatus
        self.evaluation.readiness_status = ReadinessStatus.READY
        self.evaluation.status = EvaluationStatus.COMPLETED
        self.evaluation.completed_at = timezone.now()
        self.evaluation.save(update_fields=["readiness_status", "status", "completed_at"])
        self.session.identity_verified = True
        self.session.status = "COMPLETED"
        self.session.ended_at = timezone.now()
        self.session.save(update_fields=["identity_verified", "status", "ended_at"])
        self.summary = SessionEvaluationSummary.objects.create(
            evaluation=self.evaluation, session=self.session, candidate=self.candidate,
            rule_set=ScoringRuleSet.objects.create(
                name="Cert AR Rules", version="v1", role_code="domestic_worker", role_name="Housekeeper",
                evaluation_tier=InterviewEvaluationTier.FULL, is_active=True, created_by=self.user,
            ),
            total_score=Decimal("83"), max_score=Decimal("100"), overall_percentage=Decimal("83.00"),
            layer_breakdown={
                "COGNITIVE": {"percentage": 87.0, "weight": 50},
                "BEHAVIORAL": {"percentage": 82.0, "weight": 30},
                "TASK_EXECUTION": {"percentage": 90.0, "weight": 20},
            },
            competencies_summary=[*covered_competencies()],
            total_response_count=1, evaluated_response_count=1, status=SessionEvaluationSummary.STATUS_EVALUATED,
        )
        add_minimal_response_evidence(
            evaluation=self.evaluation, session=self.session, candidate=self.candidate, user=self.user,
        )

    def test_arabic_interview_produces_an_arabic_certificate(self):
        certificate = generate_certificate(self.evaluation, self.summary)

        self.assertIsNotNone(certificate)
        pdf_bytes = certificate.pdf_file.read()
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        # A real, non-trivial PDF - not an empty/broken render.
        self.assertGreater(len(pdf_bytes), 5000)

    def test_arabic_certificate_uses_the_ar_verification_url(self):
        from unittest.mock import patch

        with patch("api.evaluations.certificate_services.render_to_string") as mock_render:
            mock_render.return_value = "<html></html>"
            generate_certificate(self.evaluation, self.summary)

        template_name, context = mock_render.call_args[0]
        self.assertEqual(template_name, "evaluations/certificate_ar.html")
        self.assertIn("/ar/verify-certificate?", context["verification_url"])
        self.assertEqual(context["language"], "ar")
        self.assertIn("arabic_font_regular_uri", context)
        self.assertEqual(context["readiness_label"], "جاهز")

    def test_english_interview_still_uses_the_english_template(self):
        self.session.candidate_language = "EN"
        self.session.save(update_fields=["candidate_language"])

        from unittest.mock import patch
        with patch("api.evaluations.certificate_services.render_to_string") as mock_render:
            mock_render.return_value = "<html></html>"
            generate_certificate(self.evaluation, self.summary)

        template_name, context = mock_render.call_args[0]
        self.assertEqual(template_name, "evaluations/certificate.html")
        self.assertIn("/en/verify-certificate?", context["verification_url"])
        self.assertEqual(context["language"], "en")
        self.assertNotIn("arabic_font_regular_uri", context)


class CertificateEligibilityTests(TestCase):
    """certificate_eligibility()/generate_certificate() must refuse to
    issue a certificate outside the documented flow: Not Ready, an
    incomplete assessment, or failed/missing identity verification must
    each independently block issuance, regardless of the others."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="cert-elig@example.com",
            password="testpass123",
            first_name="Cert",
            last_name="Elig",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Elig",
            last_name="Candidate",
            email="cert-elig-candidate@example.com",
            passport_id="CERTELIG001",
            job_role="NA",
            core_skills="safety",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=self.user,
        )
        self.config = InterviewConfiguration.objects.create(
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
        self.session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
            identity_verified=True,
        )
        self.evaluation = Evaluation.objects.create(
            session=self.session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=45,
            created_by=self.user,
            status=EvaluationStatus.COMPLETED,
            completed_at=timezone.now(),
        )
        self.consent_agreement = Agreement.objects.create(
            user=self.user,
            agreement_type=AgreementType.CANDIDATE_CONSENT,
            version="v1",
            method=AgreementMethod.CHECKBOX,
            status=AgreementStatus.SIGNED,
            accepted_at=timezone.now(),
        )
        self.session.status = "COMPLETED"
        self.session.ended_at = timezone.now()
        self.session.candidate_consent_agreement = self.consent_agreement
        self.session.save(update_fields=["status", "ended_at", "candidate_consent_agreement"])
        add_minimal_response_evidence(
            evaluation=self.evaluation, session=self.session, candidate=self.candidate, user=self.user,
        )

    def _summary(self, total_response_count=1, evaluated_response_count=1):
        return SessionEvaluationSummary.objects.create(
            evaluation=self.evaluation,
            session=self.session,
            candidate=self.candidate,
            rule_set=ScoringRuleSet.objects.create(
                name="Cert Elig Rules", version="v1", role_code="domestic_worker", role_name="Housekeeper",
                evaluation_tier=InterviewEvaluationTier.FULL, is_active=True, created_by=self.user,
            ),
            total_score=Decimal("70"), max_score=Decimal("100"), overall_percentage=Decimal("70.00"),
            competencies_summary=covered_competencies(), status=SessionEvaluationSummary.STATUS_EVALUATED,
            total_response_count=total_response_count,
            evaluated_response_count=evaluated_response_count,
        )

    def test_not_ready_gets_no_certificate(self):
        from api.core.constants import ReadinessStatus, CertificateStatus
        self.evaluation.readiness_status = ReadinessStatus.NOT_READY
        self.evaluation.save(update_fields=["readiness_status"])
        summary = self._summary()

        certificate = generate_certificate(self.evaluation, summary)

        self.assertIsNone(certificate)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.NOT_ISSUED)
        self.assertFalse(Certificate.objects.filter(evaluation=self.evaluation).exists())

    def test_incomplete_assessment_gets_no_certificate_even_if_ready(self):
        from api.core.constants import ReadinessStatus, CertificateStatus
        self.evaluation.readiness_status = ReadinessStatus.READY
        self.evaluation.save(update_fields=["readiness_status"])
        summary = self._summary(total_response_count=4, evaluated_response_count=2)

        certificate = generate_certificate(self.evaluation, summary)

        self.assertIsNone(certificate)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.NOT_ISSUED)

    def test_unverified_identity_gets_no_certificate_even_if_ready_and_complete(self):
        from api.core.constants import ReadinessStatus, CertificateStatus
        self.evaluation.readiness_status = ReadinessStatus.READY
        self.evaluation.save(update_fields=["readiness_status"])
        self.session.identity_verified = False
        self.session.save(update_fields=["identity_verified"])
        summary = self._summary()

        certificate = generate_certificate(self.evaluation, summary)

        self.assertIsNone(certificate)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.NOT_ISSUED)

    def test_certificate_requires_signed_consent(self):
        from api.core.constants import CertificateStatus
        self.session.candidate_consent_agreement = None
        self.session.save(update_fields=["candidate_consent_agreement"])
        summary = self._summary()

        eligible, reason = certificate_eligibility(self.evaluation, summary)
        certificate = generate_certificate(self.evaluation, summary)

        self.assertFalse(eligible)
        self.assertEqual(reason, "CONSENT_REQUIRED")
        self.assertIsNone(certificate)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.NOT_ISSUED)

    def test_unsigned_consent_gets_no_certificate(self):
        from api.core.constants import CertificateStatus
        self.consent_agreement.status = AgreementStatus.PENDING
        self.consent_agreement.save(update_fields=["status"])
        summary = self._summary()

        eligible, reason = certificate_eligibility(self.evaluation, summary)
        certificate = generate_certificate(self.evaluation, summary)

        self.assertFalse(eligible)
        self.assertEqual(reason, "CONSENT_REQUIRED")
        self.assertIsNone(certificate)

    def test_signed_consent_allows_certificate(self):
        from api.core.constants import CertificateStatus
        summary = self._summary()

        eligible, reason = certificate_eligibility(self.evaluation, summary)
        certificate = generate_certificate(self.evaluation, summary)

        self.assertTrue(eligible)
        self.assertIsNotNone(certificate)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.ISSUED)

    def test_partially_ready_complete_and_verified_gets_a_certificate(self):
        # PARTIALLY_READY isn't a stored readiness_status value (only READY/
        # NOT_READY/PENDING/INCOMPLETE are) - it's the resolved display
        # classification for PENDING/unmatched, same as the internal report
        # - see EvaluationReportService._resolve_readiness_indicator.
        from api.core.constants import CertificateStatus
        summary = self._summary()

        certificate = generate_certificate(self.evaluation, summary)

        self.assertIsNotNone(certificate)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.ISSUED)

    def test_scheduled_interview_without_rating_gets_no_certificate(self):
        from api.core.constants import CertificateStatus
        self.session.scheduled_start_at = timezone.now() - timezone.timedelta(days=1)
        self.session.save(update_fields=["scheduled_start_at"])
        summary = self._summary()

        eligible, reason = certificate_eligibility(self.evaluation, summary)
        certificate = generate_certificate(self.evaluation, summary)

        self.assertFalse(eligible)
        self.assertEqual(reason, "EVALUATOR_RATING_PENDING")
        self.assertIsNone(certificate)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.NOT_ISSUED)

    def test_scheduled_interview_with_rating_gets_a_certificate(self):
        from api.core.constants import CertificateStatus
        self.session.scheduled_start_at = timezone.now() - timezone.timedelta(days=1)
        self.session.save(update_fields=["scheduled_start_at"])
        EvaluatorRating.objects.create(
            evaluation=self.evaluation,
            safety_awareness=80, hygiene=75, communication=85, behavior_integrity=70, task_execution=60,
            rated_by=self.user,
        )
        summary = self._summary()

        eligible, reason = certificate_eligibility(self.evaluation, summary)
        certificate = generate_certificate(self.evaluation, summary)

        self.assertTrue(eligible)
        self.assertIsNotNone(certificate)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.ISSUED)

    def test_ai_interview_without_rating_still_gets_a_certificate(self):
        # Regression guard: the new evaluator-rating gate must not affect
        # AI Interview mode (no scheduled_start_at) evaluations at all.
        from api.core.constants import CertificateStatus
        summary = self._summary()

        eligible, reason = certificate_eligibility(self.evaluation, summary)
        certificate = generate_certificate(self.evaluation, summary)

        self.assertTrue(eligible)
        self.assertNotEqual(reason, "EVALUATOR_RATING_PENDING")
        self.assertIsNotNone(certificate)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.ISSUED)

    def test_certificate_verify_reflects_revocation_live(self):
        from api.evaluations.certificate_services import _revoke_existing_certificate
        summary = self._summary()
        certificate = generate_certificate(self.evaluation, summary)
        client = APIClient()

        before = client.get(f"/api/v1/evaluations/certificates/verify/{certificate.verification_id}")
        self.assertEqual(before.data["status"], "VALID")

        _revoke_existing_certificate(self.evaluation, "test revocation")

        after = client.get(f"/api/v1/evaluations/certificates/verify/{certificate.verification_id}")
        self.assertEqual(after.data["status"], "REVOKED")

    def test_certificate_verify_rejects_the_sequential_certificate_id(self):
        # The human-readable certificate_id (ML-YYYY-NNNNNN) is sequential
        # and must never work as the public verification lookup key - only
        # the opaque verification_id may (Knowledge Layer security spec
        # v1.1 section 6.2: verification identifiers must be non-sequential
        # and non-guessable).
        summary = self._summary()
        certificate = generate_certificate(self.evaluation, summary)
        client = APIClient()

        response = client.get(f"/api/v1/evaluations/certificates/verify/{certificate.certificate_id}")
        self.assertEqual(response.status_code, 404)

    def test_certificate_issuance_and_revocation_are_audit_logged(self):
        from api.evaluations.certificate_services import _revoke_existing_certificate

        summary = self._summary()
        generate_certificate(self.evaluation, summary)

        self.assertTrue(
            AuditLog.objects.filter(action=AuditLogAction.CERTIFICATE_ISSUED).exists()
        )

        _revoke_existing_certificate(self.evaluation, "test revocation")

        self.assertTrue(
            AuditLog.objects.filter(action=AuditLogAction.CERTIFICATE_REVOKED).exists()
        )

    def test_retaking_the_same_role_supersedes_the_earlier_certificate(self):
        # A candidate retaking the same role gets a brand new Evaluation
        # (Certificate is one-to-one with Evaluation, so it gets its own
        # certificate too) - the older certificate for that candidate+role
        # must read as SUPERSEDED, not stay VALID forever (Knowledge Layer
        # security spec v1.1 section 6.3).
        from api.core.constants import CertificateStatus

        summary = self._summary()
        first_certificate = generate_certificate(self.evaluation, summary)
        self.assertIsNotNone(first_certificate)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.ISSUED)

        second_session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
            identity_verified=True,
            status="COMPLETED",
            ended_at=timezone.now(),
            candidate_consent_agreement=self.consent_agreement,
        )
        second_evaluation = Evaluation.objects.create(
            session=second_session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=45,
            created_by=self.user,
            status=EvaluationStatus.COMPLETED,
            completed_at=timezone.now(),
            candidate_job_role=self.evaluation.candidate_job_role,
        )
        add_minimal_response_evidence(
            evaluation=second_evaluation, session=second_session, candidate=self.candidate, user=self.user,
        )
        second_summary = SessionEvaluationSummary.objects.create(
            evaluation=second_evaluation,
            session=second_session,
            candidate=self.candidate,
            rule_set=ScoringRuleSet.objects.create(
                name="Cert Elig Rules Retake", version="v1", role_code="domestic_worker", role_name="Housekeeper",
                evaluation_tier=InterviewEvaluationTier.FULL, is_active=True, created_by=self.user,
            ),
            total_score=Decimal("80"), max_score=Decimal("100"), overall_percentage=Decimal("80.00"),
            competencies_summary=covered_competencies(), status=SessionEvaluationSummary.STATUS_EVALUATED,
            total_response_count=1,
            evaluated_response_count=1,
        )

        second_certificate = generate_certificate(second_evaluation, second_summary)
        self.assertIsNotNone(second_certificate)

        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.SUPERSEDED)

        second_evaluation.refresh_from_db()
        self.assertEqual(second_evaluation.certificate_status, CertificateStatus.ISSUED)

        self.assertTrue(
            AuditLog.objects.filter(action=AuditLogAction.CERTIFICATE_SUPERSEDED).exists()
        )

        client = APIClient()
        response = client.get(f"/api/v1/evaluations/certificates/verify/{first_certificate.verification_id}")
        self.assertEqual(response.data["status"], "SUPERSEDED")


class EvaluatorRatingTests(TestCase):
    """The evaluator's manual 0-100 rating on the 5 approved dimensions is
    purely additive: it must never change Evaluation.score/readiness_status,
    and must only trigger certificate regeneration when a certificate was
    already issued - never first-issue one on its own."""

    VALID_RATINGS = {
        "safety_awareness": 80,
        "hygiene": 75,
        "communication": 85,
        "behavior_integrity": 70,
        "task_execution": 60,
    }

    def setUp(self):
        self.user = User.objects.create_user(
            email="rating-owner@example.com",
            password="testpass123",
            first_name="Rating",
            last_name="Owner",
            role=Roles.B2C,
            is_verified=True,
        )
        self.other_user = User.objects.create_user(
            email="rating-other@example.com",
            password="testpass123",
            first_name="Other",
            last_name="Owner",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Rating",
            last_name="Candidate",
            email="rating-candidate@example.com",
            passport_id="RATING001",
            job_role="NA",
            core_skills="safety",
            preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=self.user,
        )
        self.config = InterviewConfiguration.objects.create(
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
        self.session = InterviewSession.objects.create(
            candidate=self.candidate,
            organization=self.candidate.company,
            config=self.config,
            role_name=self.config.role_name,
            role_code=self.config.role_code,
            ui_language="EN",
            candidate_language="EN",
            tts_language_code="en-US",
            stt_language_code="en-US",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0",
            question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
            identity_verified=True,
            status="COMPLETED",
            ended_at=timezone.now(),
        )
        self.evaluation = Evaluation.objects.create(
            session=self.session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=45,
            created_by=self.user,
            status=EvaluationStatus.COMPLETED,
            completed_at=timezone.now(),
        )
        consent_agreement = Agreement.objects.create(
            user=self.user,
            agreement_type=AgreementType.CANDIDATE_CONSENT,
            version="v1",
            method=AgreementMethod.CHECKBOX,
            status=AgreementStatus.SIGNED,
            accepted_at=timezone.now(),
        )
        self.session.candidate_consent_agreement = consent_agreement
        self.session.save(update_fields=["candidate_consent_agreement"])
        self.url = f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/evaluator-rating"
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _summary(self):
        return SessionEvaluationSummary.objects.create(
            evaluation=self.evaluation,
            session=self.session,
            candidate=self.candidate,
            rule_set=ScoringRuleSet.objects.create(
                name="Rating Test Rules", version="v1", role_code="domestic_worker", role_name="Housekeeper",
                evaluation_tier=InterviewEvaluationTier.FULL, is_active=True, created_by=self.user,
            ),
            total_score=Decimal("70"), max_score=Decimal("100"), overall_percentage=Decimal("70.00"),
            competencies_summary=covered_competencies(), status=SessionEvaluationSummary.STATUS_EVALUATED,
            total_response_count=1, evaluated_response_count=1,
        )

    def test_get_returns_404_before_any_rating_submitted(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_get_does_not_crash_on_pre_migration_rating_with_null_dimensions(self):
        # Regression test: a production incident where GET (and any list
        # endpoint serializing EvaluatorRating) 500'd for every rating
        # submitted before hygiene/communication existed, because the
        # consistency fallback did Decimal(str(None)) on those None
        # fields. hygiene/communication are left unset here to reproduce
        # a pre-migration row.
        EvaluatorRating.objects.create(
            evaluation=self.evaluation,
            safety_awareness=80, behavior_integrity=70, task_execution=60,
            rated_by=self.user,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["hygiene"])
        self.assertIsNone(response.data["communication"])

    def test_post_creates_a_rating(self):
        response = self.client.post(self.url, self.VALID_RATINGS, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["consistency"], 91)
        self.assertEqual(EvaluatorRating.objects.count(), 1)
        rating = EvaluatorRating.objects.get(evaluation=self.evaluation)
        self.assertEqual(rating.safety_awareness, 80)
        self.assertEqual(rating.rated_by, self.user)

    def test_resubmission_updates_the_same_row(self):
        self.client.post(self.url, self.VALID_RATINGS, format="json")
        first_rated_at = EvaluatorRating.objects.get(evaluation=self.evaluation).rated_at

        updated = {**self.VALID_RATINGS, "safety_awareness": 45}
        response = self.client.post(self.url, updated, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(EvaluatorRating.objects.count(), 1)
        rating = EvaluatorRating.objects.get(evaluation=self.evaluation)
        self.assertEqual(rating.safety_awareness, 45)
        self.assertGreaterEqual(rating.rated_at, first_rated_at)

    def test_out_of_range_values_are_rejected(self):
        too_high = {**self.VALID_RATINGS, "safety_awareness": 101}
        response = self.client.post(self.url, too_high, format="json")
        self.assertEqual(response.status_code, 400)

        too_low = {**self.VALID_RATINGS, "task_execution": -1}
        response = self.client.post(self.url, too_low, format="json")
        self.assertEqual(response.status_code, 400)

        self.assertEqual(EvaluatorRating.objects.count(), 0)

    def test_missing_field_is_rejected(self):
        incomplete = {k: v for k, v in self.VALID_RATINGS.items() if k != "task_execution"}
        response = self.client.post(self.url, incomplete, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(EvaluatorRating.objects.count(), 0)

    def test_user_without_manage_access_is_denied(self):
        # A B2C stranger is excluded by _accessible_evaluations_queryset
        # before CanManageEvaluation's object check even runs, so DRF's
        # generic get_object() 404s rather than 403ing - same ambiguity
        # already accepted by test_non_owner_cannot_reschedule_or_cancel
        # above for the sibling reschedule/cancel actions.
        self.client.force_authenticate(self.other_user)
        response = self.client.post(self.url, self.VALID_RATINGS, format="json")
        self.assertIn(response.status_code, {403, 404})
        self.assertEqual(EvaluatorRating.objects.count(), 0)

    def test_submitting_a_rating_does_not_alter_score_or_readiness(self):
        summary = self._summary()
        Week6ScoringService._apply_evaluation_rollups(evaluation=self.evaluation, summary=summary, actor=self.user)
        self.evaluation.refresh_from_db()
        original_score = self.evaluation.score
        original_readiness = self.evaluation.readiness_status
        original_cert_status = self.evaluation.certificate_status

        response = self.client.post(self.url, self.VALID_RATINGS, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.score, original_score)
        self.assertEqual(self.evaluation.readiness_status, original_readiness)
        self.assertEqual(self.evaluation.certificate_status, original_cert_status)

    def test_submitting_a_rating_does_not_first_issue_a_certificate(self):
        # certificate_status stays NOT_ISSUED - no summary/certificate exists yet,
        # so there's nothing eligible for submit_evaluator_rating to regenerate.
        from api.core.constants import CertificateStatus
        response = self.client.post(self.url, self.VALID_RATINGS, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.certificate_status, CertificateStatus.NOT_ISSUED)
        self.assertFalse(Certificate.objects.filter(evaluation=self.evaluation).exists())

    def test_submitting_a_rating_regenerates_an_already_issued_certificate(self):
        summary = self._summary()
        add_minimal_response_evidence(
            evaluation=self.evaluation, session=self.session, candidate=self.candidate, user=self.user,
        )
        certificate = generate_certificate(self.evaluation, summary)
        self.assertIsNotNone(certificate)
        original_hash = certificate.pdf_hash

        response = self.client.post(self.url, self.VALID_RATINGS, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        certificate.refresh_from_db()
        self.assertNotEqual(certificate.pdf_hash, original_hash)

    def test_report_payload_reflects_rating_state_at_generation_time(self):
        from api.reports.services import EvaluationReportService

        self.assertIsNone(EvaluationReportService._build_evaluator_rating(self.evaluation))

        self.client.post(self.url, self.VALID_RATINGS, format="json")
        self.evaluation.refresh_from_db()

        payload = EvaluationReportService._build_evaluator_rating(self.evaluation)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["safety_awareness"], 80)
        self.assertEqual(payload["hygiene"], 75)
        self.assertEqual(payload["communication"], 85)
        self.assertEqual(payload["task_execution"], 60)
        self.assertEqual(payload["behavioral_risk_level"], "Medium")
        self.assertEqual(payload["consistency"], 91)


class InsufficientEvidenceReadinessMappingTests(TestCase):
    """EvaluationReadinessRecordService and EvaluationReportService must
    agree on the new INCOMPLETE state's round trip, the same way they
    already agree on READY/NOT_READY/PENDING."""

    def test_readiness_record_service_round_trips_incomplete(self):
        from api.evaluations.readiness_record_services import EvaluationReadinessRecordService

        indicator = EvaluationReadinessRecordService._map_indicator(ReadinessStatus.INCOMPLETE)
        self.assertEqual(indicator, "أدلة غير كافية")
        self.assertEqual(
            EvaluationReadinessRecordService.status_from_indicator(indicator),
            ReadinessStatus.INCOMPLETE,
        )

    def test_report_service_resolves_incomplete_to_its_own_distinct_code(self):
        from api.reports.services import EvaluationReportService

        evaluation = SimpleNamespace(readiness_status=ReadinessStatus.INCOMPLETE)
        resolved = EvaluationReportService._resolve_readiness_indicator(evaluation, readiness_record=None)

        self.assertEqual(resolved["code"], "INCOMPLETE")
        self.assertNotEqual(resolved["code"], "PARTIALLY_READY")
        self.assertNotEqual(resolved["code"], "NOT_READY")
