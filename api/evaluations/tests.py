from django.test import TestCase
from django.utils import timezone

from api.accounts.models import User
from api.candidates.models import Candidate
from api.core.constants import (
    CandidateResponseType,
    EvaluationType,
    InterviewEvaluationTier,
    QuestionDifficulty,
    QuestionLifecycleStatus,
    ReadinessStatus,
    Roles,
)
from api.evaluations.models import Evaluation
from api.questions.models import QuestionTemplate
from api.scores.models import CandidateScore, ScoreSet
from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion
from api.interviews.models import InterviewConfiguration


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
