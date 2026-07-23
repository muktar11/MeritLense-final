from django.test import TestCase
from django.db import DatabaseError
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.audit.models import AuditLog
from api.candidates.models import Candidate
from api.core.constants import AuditLogAction, CandidateResponseType, CoverageLevel, EvaluationType, InterviewEvaluationTier, QuestionDifficulty, QuestionLifecycleStatus, ReadinessStatus, Roles
from api.evaluations.models import Evaluation, EvaluationReadinessDecisionRecord, ResponseEvaluationResult, ScoringRule, ScoringRuleSet, SessionEvaluationSummary
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
