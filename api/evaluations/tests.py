from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from api.accounts.models import User
from api.candidates.models import Candidate
from api.core.constants import CandidateResponseType, CoverageLevel, EvaluationType, InterviewEvaluationTier, QuestionDifficulty, QuestionLifecycleStatus, ReadinessStatus, Roles
from api.evaluations.models import Evaluation, ResponseEvaluationResult, ScoringRule, ScoringRuleSet, SessionEvaluationSummary
from api.evaluations.scoring_services import Week6ScoringService
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
