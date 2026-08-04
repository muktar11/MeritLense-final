from decimal import Decimal

from django.test import TestCase, override_settings
from django.db import DatabaseError
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient
from urllib.parse import parse_qs, urlsplit

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.audit.models import AuditLog
from api.candidates.models import Candidate
from api.core.constants import AuditLogAction, CandidateResponseType, CoverageLevel, EvaluationLayer, EvaluationStatus, EvaluationType, InterviewEvaluationTier, QuestionDifficulty, QuestionLifecycleStatus, ReadinessStatus, Roles, SubscriptionStatus, BillingInterval
from api.evaluations.models import CompetencyEvaluationResult, Evaluation, EvaluationReadinessDecisionRecord, ResponseEvaluationResult, ScoringRule, ScoringRuleSet, SessionEvaluationSummary
from api.evaluations.scoring_services import Week6ScoringService
from api.payments.models import Customer, Price, Subscription
from api.questions.models import QuestionTemplate
from api.scores.models import CandidateScore, ScoreSet
from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion
from api.interviews.models import InterviewConfiguration
from api.translation.models import EvaluationInputArtifact


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
        self.assertEqual(self.evaluation.readiness_status, ReadinessStatus.READY)
        self.assertEqual(float(self.evaluation.score), 70.0)
        record = EvaluationReadinessDecisionRecord.objects.get(evaluation=self.evaluation)
        self.assertEqual(record.readiness_indicator, "جاهز")
        self.assertFalse(record.override_triggered)
        self.assertEqual(record.rule_engine_version, "v1.0")
        self.assertEqual(record.session, self.session)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLogAction.RULE_ENGINE_DECISION_RECORDED,
                resource_id=self.evaluation.id,
            ).exists()
        )

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
        self.assertEqual(legal_record_response.data["readiness_indicator"], "جاهز")

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
