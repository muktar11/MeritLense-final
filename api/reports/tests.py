import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api.accounts.models import User
from api.audit.models import AuditLog
from api.candidates.models import Candidate
from api.core.constants import (
    AuditLogAction,
    CandidateResponseType,
    EvaluationType,
    InterviewEvaluationTier,
    QuestionDifficulty,
    QuestionLifecycleStatus,
    Roles,
)
from api.evaluations.models import Evaluation, ScoringRule, ScoringRuleSet
from api.evaluations.scoring_services import Week6ScoringService
from api.interviews.models import InterviewConfiguration
from api.questions.models import QuestionTemplate
from api.reports.models import EvaluationReport
from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion
from api.translation.models import (
    CandidateResponseInterpretation,
    CandidateResponseTranslation,
    EvaluationInputArtifact,
)


class EvaluationReportApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_dir = tempfile.mkdtemp(prefix="report-tests-")
        cls._override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="week7@example.com",
            password="testpass123",
            first_name="Week",
            last_name="Seven",
            role=Roles.B2C,
            is_verified=True,
        )
        self.other_user = User.objects.create_user(
            email="blocked@example.com",
            password="testpass123",
            first_name="Blocked",
            last_name="User",
            role=Roles.B2C,
            is_verified=True,
        )
        self.client.force_authenticate(self.user)
        self.candidate = Candidate.objects.create(
            first_name="Report",
            last_name="Candidate",
            email="report@example.com",
            passport_id="REPORT-001",
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
            enable_translation=True,
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
            candidate_language="AR",
            tts_language_code="en-US",
            stt_language_code="ar-SA",
            translation_target="EN",
            total_questions=1,
            evaluation_tier=InterviewEvaluationTier.FULL,
            package_code="premium",
            package_name="Premium",
            rubric_version="v2.0",
            question_set_version="v1.2",
            started_at=timezone.now(),
            ended_at=timezone.now(),
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
            status="COMPLETED",
        )
        self.template = QuestionTemplate.objects.create(
            role_name="Housekeeper",
            role_code="domestic_worker",
            question_code="HK-SAF-003",
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Safety & Hygiene",
            skill_tag="safety_awareness",
            skill="Safety Awareness",
            sequence_number=1,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text="What do you do when a child slips near a spill?",
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
            transcript="I would move the child away, dry the floor, and warn others.",
            original_transcript="سأبعد الطفل وأنشف الأرض وأنبه الآخرين",
            transcript_language="ar",
            translated_transcript="I would move the child away, dry the floor, and warn others.",
            translation_status="COMPLETED",
            text_response="I would move the child away, dry the floor, and warn others.",
            interpretation_status="COMPLETED",
            processing_status="RULE_INPUT_PREPARED",
            stt_status="COMPLETED",
            stt_confidence="0.9100",
        )
        CandidateResponseTranslation.objects.create(
            response=self.response,
            session=self.session,
            question=self.session_question,
            source_language="AR",
            target_language="EN",
            original_transcript=self.response.original_transcript,
            translated_transcript=self.response.translated_transcript,
            provider="fake-translate",
            provider_model="v1",
            status="COMPLETED",
        )
        CandidateResponseInterpretation.objects.create(
            response=self.response,
            session=self.session,
            question=self.session_question,
            provider="fake-interpret",
            model="v1",
            status="COMPLETED",
            confidence_score="0.620",
            structured_output={
                "transcript_issues": ["background noise detected"],
            },
        )
        EvaluationInputArtifact.objects.create(
            response=self.response,
            session=self.session,
            question=self.session_question,
            competency_code="safety_awareness",
            expected_indicators=["move child away", "dry floor", "warn others"],
            observed_indicators=["move child away", "dry floor"],
            missing_indicators=["warn others"],
            risk_flags=[],
            source_interpretation_status="COMPLETED",
            requires_human_review=True,
            review_reason="Interpretation confidence is below the configured threshold.",
            metadata={"source": "week5"},
        )
        self.evaluation = Evaluation.objects.create(
            session=self.session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=45,
            created_by=self.user,
            status="COMPLETED",
            completed_at=timezone.now(),
        )
        self.rule_set = ScoringRuleSet.objects.create(
            name="Week 7 Default",
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
            question_code="HK-SAF-003",
            expected_indicators=["move child away", "dry floor", "warn others"],
            required_indicators=["move child away"],
            weighted_indicators={
                "move child away": "4",
                "dry floor": "3",
                "warn others": "3",
            },
            max_score="10.00",
            pass_threshold="8.00",
            scoring_method=ScoringRule.SCORING_METHOD_WEIGHTED_MATCH,
            is_active=True,
        )
        Week6ScoringService.run_for_evaluation(
            evaluation=self.evaluation,
            actor=self.user,
            rule_set=self.rule_set,
        )

    def test_generate_report_persists_payload_and_exposes_endpoints(self):
        response = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["report_status"], EvaluationReport.STATUS_ACTIVE)
        self.assertEqual(response.data["report_version"], "1.3")
        self.assertEqual(response.data["scoring_rule_version"], "week6-v1")
        self.assertEqual(response.data["rule_engine_version"], "v1.0")
        self.assertFalse(response.data["override_triggered"])
        self.assertEqual(response.data["readiness_indicator"], "جاهزية جزئية")
        self.assertTrue(response.data["requires_human_review"])
        self.assertEqual(response.data["report_payload"]["assessment_context"]["assessment_status"], "COMPLETED")
        self.assertEqual(response.data["report_payload"]["assessment_context"]["assessment_quality"], "Limited")
        self.assertEqual(response.data["report_payload"]["executive_summary"]["readiness_indicator"]["code"], "PARTIALLY_READY")
        self.assertEqual(
            list(response.data["report_payload"]["executive_summary"].keys())[:2],
            ["readiness_indicator", "overall_score"],
        )
        self.assertEqual(
            response.data["report_payload"]["executive_summary"]["assessment_scope"],
            "Pre-employment Workforce Readiness Only",
        )
        self.assertIn(
            "Complete interview",
            response.data["report_payload"]["executive_summary"]["reliability_factors"],
        )
        self.assertIn(
            "Response consistency requires review",
            response.data["report_payload"]["executive_summary"]["reliability_factors"],
        )
        self.assertEqual(response.data["report_payload"]["rule_engine_version"], "v1.0")
        self.assertEqual(
            response.data["report_payload"]["legal_record_id"],
            str(self.evaluation.readiness_legal_record.public_id),
        )
        self.assertEqual(
            response.data["report_payload"]["legal_disclaimer"],
            "This report provides decision support only and does not constitute an employment decision. Final hiring decisions remain with the employer.",
        )
        self.assertEqual(
            response.data["report_payload"]["evaluation_flow_reference"],
            "Interview Session -> Responses -> AI Processing -> Deterministic Scoring -> Rule Engine -> Evaluation Report",
        )
        self.assertEqual(response.data["report_payload"]["assessment_context"]["assessment_coverage"][0], "Safety")
        self.assertIn("transcript_report", response.data["report_payload"])
        self.assertEqual(
            response.data["report_payload"]["transcript_report"]["evaluation_bands"][0]["code"],
            "COGNITIVE",
        )
        self.assertTrue(response.data["report_payload"]["transcript_report"]["role_fit"])
        self.assertEqual(
            response.data["report_payload"]["identity_verification"]["verification_status"],
            "NOT_STARTED",
        )
        self.assertEqual(response.data["report_payload"]["verification_status"], "Authentic")
        self.assertTrue(response.data["report_payload"]["document_integrity"]["hash_value"])
        self.assertEqual(response.data["response_evidence_summary"][0]["traceability"]["translation_reference"]["status"], "COMPLETED")

        report = EvaluationReport.objects.get(evaluation=self.evaluation, report_status=EvaluationReport.STATUS_ACTIVE)
        self.assertEqual(report.report_payload["report_name"], "MeritLense Workforce Readiness Assessment Report")
        self.assertEqual(report.report_version, "1.3")
        self.assertEqual(report.rule_engine_version, "v1.0")
        self.assertEqual(report.readiness_indicator, "جاهزية جزئية")
        self.assertFalse(report.override_triggered)
        self.assertEqual(report.readiness_legal_record, self.evaluation.readiness_legal_record)
        self.assertTrue(report.employer_pdf.name.endswith(".pdf"))
        self.assertTrue(report.pdf_hash)

        latest_report = self.client.get(f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/report")
        self.assertEqual(latest_report.status_code, 200)
        self.assertEqual(latest_report.data["id"], str(report.public_id))
        self.assertTrue(latest_report.data["employer_pdf_url"].endswith(".pdf"))

        report_detail = self.client.get(f"/api/v1/evaluations/reports/{report.public_id}")
        self.assertEqual(report_detail.status_code, 200)

        export_payload = self.client.get(f"/api/v1/evaluations/reports/{report.public_id}/export-payload")
        self.assertEqual(export_payload.status_code, 200)
        self.assertIn("competency_breakdown", export_payload.data)
        self.assertIn("evaluation_flow_reference", export_payload.data)
        self.assertIn("document_integrity", export_payload.data)

        employer_payload = self.client.get(f"/api/v1/evaluations/reports/{report.public_id}/export-employer-payload")
        self.assertEqual(employer_payload.status_code, 200)
        self.assertNotIn("candidate_id", employer_payload.data)
        self.assertNotIn("critical_failures", employer_payload.data)

        export_pdf = self.client.get(f"/api/v1/evaluations/reports/{report.public_id}/export-pdf")
        self.assertEqual(export_pdf.status_code, 200)
        self.assertEqual(export_pdf["Content-Type"], "application/pdf")

        verify_response = self.client.get(f"/api/v1/evaluations/reports/verify/{report.report_number}")
        self.assertEqual(verify_response.status_code, 200)
        self.assertEqual(verify_response.json()["verification_status"], "Authentic")
        self.assertEqual(verify_response.json()["sha256_hash"], report.pdf_hash)

        interview_report = self.client.get(f"/api/v1/interviews/{self.session.public_id}/report/")
        self.assertEqual(interview_report.status_code, 200)
        self.assertEqual(interview_report.data["id"], str(report.public_id))

        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.REPORT_GENERATION_STARTED).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLogAction.REPORT_EXPORT_PAYLOAD_REQUESTED,
                resource_id=report.id,
            ).exists()
        )

    def test_candidate_score_summary_endpoint_returns_report_metadata(self):
        self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )
        report = EvaluationReport.objects.get(evaluation=self.evaluation, report_status=EvaluationReport.STATUS_ACTIVE)

        response = self.client.get("/api/v1/evaluations/candidate-scores")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        summary = response.data[0]
        self.assertEqual(summary["candidate_id"], str(self.candidate.public_id))
        self.assertEqual(summary["evaluation_id"], str(self.evaluation.public_id))
        self.assertEqual(summary["status"], "REQUIRES_HUMAN_REVIEW")
        self.assertEqual(summary["certificate"], None)
        self.assertEqual(summary["report"]["report_id"], str(report.public_id))
        self.assertEqual(summary["report"]["report_number"], report.report_number)
        self.assertEqual(summary["report"]["report_status"], EvaluationReport.STATUS_ACTIVE)
        self.assertTrue(summary["report"]["pdf_url"].endswith(".pdf"))
        self.assertEqual(summary["competencies"][0]["code"], "safety_awareness")

    def test_regenerate_report_marks_previous_one_stale_and_keeps_history(self):
        first = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )
        first_report_id = first.data["id"]
        first_report = EvaluationReport.objects.get(public_id=first_report_id)

        regenerate = self.client.post(f"/api/v1/evaluations/reports/{first_report.public_id}/regenerate", {}, format="json")

        self.assertEqual(regenerate.status_code, 200)
        self.assertNotEqual(regenerate.data["id"], first.data["id"])
        first_report.refresh_from_db()
        self.assertEqual(first_report.report_status, EvaluationReport.STATUS_SUPERSEDED)
        self.assertEqual(EvaluationReport.objects.filter(evaluation=self.evaluation).count(), 2)
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.PREVIOUS_REPORT_MARKED_STALE).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.REPORT_REGENERATED).exists())

    def test_report_is_immutable_except_for_controlled_stale_transition(self):
        generate = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )
        report = EvaluationReport.objects.get(public_id=generate.data["id"])

        report.report_payload["tampered"] = True
        with self.assertRaises(ValidationError):
            report.save()

        with self.assertRaises(ValidationError):
            report.delete()

        with self.assertRaises(DatabaseError):
            EvaluationReport.objects.filter(pk=report.pk).update(
                readiness_reason="changed later",
            )

        with self.assertRaises(DatabaseError):
            EvaluationReport.objects.filter(pk=report.pk).delete()

    def test_generate_report_fails_when_scoring_summary_missing(self):
        new_session = InterviewSession.objects.create(
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
            expires_at=InterviewSession.build_expiry(30),
            created_by=self.user,
        )
        new_evaluation = Evaluation.objects.create(
            session=new_session,
            candidate=self.candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=2),
            duration_minutes=45,
            created_by=self.user,
            status="COMPLETED",
            completed_at=timezone.now(),
        )
        new_session.status = "COMPLETED"
        new_session.ended_at = timezone.now()
        new_session.save(update_fields=["status", "ended_at", "updated_at"])

        response = self.client.post(
            f"/api/v1/evaluations/evaluations/{new_evaluation.public_id}/generate-report",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("scoring summary", response.data["detail"].lower())
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.REPORT_GENERATION_FAILED).exists())

    def test_unauthorized_user_cannot_view_report(self):
        generate = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )
        report_id = generate.data["id"]

        self.client.force_authenticate(self.other_user)
        response = self.client.get(f"/api/v1/evaluations/reports/{report_id}")

        self.assertEqual(response.status_code, 404)

    def test_session_token_does_not_expose_interview_report(self):
        self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )
        anonymous = APIClient()
        response = anonymous.get(
            f"/api/v1/interviews/{self.session.public_id}/report/",
            HTTP_X_SESSION_TOKEN=self.session.access_token,
        )

        self.assertEqual(response.status_code, 403)
