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
from api.contracts.models import Agreement
from api.core.constants import (
    AgreementMethod,
    AgreementStatus,
    AgreementType,
    AuditLogAction,
    CandidateResponseType,
    EvaluationType,
    InterviewEvaluationTier,
    QuestionDifficulty,
    QuestionLifecycleStatus,
    Roles,
)
from api.evaluations.models import (
    Evaluation,
    EvaluatorRating,
    ResponseEvaluationResult,
    ScoringRule,
    ScoringRuleSet,
    SessionEvaluationSummary,
)
from api.evaluations.scoring_services import Week6ScoringService
from api.interviews.models import InterviewConfiguration
from api.questions.models import QuestionTemplate
from api.reports.models import EvaluationReport
from api.reports.services import EvaluationReportService
from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion
from api.translation.models import (
    CandidateResponseInterpretation,
    CandidateResponseTranslation,
    EvaluationInputArtifact,
)
from api.reports.services import EvaluationReportService


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
        self.assertEqual(
            response.data["report_payload"]["assessment_context"]["candidate_name"],
            self.candidate.get_full_name(),
        )
        self.assertEqual(
            response.data["report_payload"]["assessment_context"]["candidate_email"],
            self.candidate.email,
        )
        self.assertEqual(
            response.data["report_payload"]["assessment_context"]["candidate_passport_id"],
            self.candidate.passport_id,
        )
        self.assertEqual(response.data["report_payload"]["executive_summary"]["readiness_indicator"]["code"], "PARTIALLY_READY")
        self.assertEqual(
            list(response.data["report_payload"]["executive_summary"].keys())[:2],
            ["readiness_indicator", "overall_score"],
        )
        # Only one of the five canonical competencies (Safety) has any
        # evidence in this fixture - exactly the scenario a plain response-
        # scoring completeness check misses (all submitted responses WERE
        # scored, so completeness reads 100%), so the overall score must
        # stay withheld until enough competencies are actually covered.
        self.assertEqual(
            response.data["report_payload"]["executive_summary"]["overall_score_display"],
            "Not fully available",
        )
        self.assertFalse(response.data["report_payload"]["executive_summary"]["overall_score_available"])
        self.assertEqual(response.data["report_payload"]["assessment_context"]["competencies_assessed_count"], 1)
        self.assertEqual(response.data["report_payload"]["assessment_context"]["competencies_required_count"], 5)
        self.assertEqual(
            response.data["report_payload"]["executive_summary"]["assessment_scope"],
            "Pre-employment Workforce Readiness Only",
        )
        self.assertEqual(
            response.data["report_payload"]["executive_summary"]["top_strengths"][0],
            "No significant strengths identified in this assessment.",
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
        self.assertTrue(
            response.data["report_payload"]["qr_verification_url"].startswith(
                "https://www.meritlense.com/en/verify-report?id="
            )
        )
        self.assertEqual(
            response.data["report_payload"]["candidate_snapshot"]["full_name"],
            self.candidate.get_full_name(),
        )
        self.assertEqual(response.data["report_payload"]["assessment_context"]["assessment_coverage"][0]["label"], "Safety")
        self.assertTrue(response.data["report_payload"]["assessment_context"]["assessment_coverage"][0]["covered"])
        self.assertFalse(response.data["report_payload"]["assessment_context"]["assessment_coverage"][-1]["covered"])
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
        self.assertEqual(
            response.data["report_payload"]["identity_verification"]["employer_status"],
            "Not Completed",
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
        self.assertNotIn("api_schema_version", employer_payload.data)
        self.assertNotIn("legal_record_id", employer_payload.data)
        self.assertNotIn("assessment_framework_version", employer_payload.data)
        self.assertNotIn("role_profile_version", employer_payload.data)
        self.assertNotIn("rule_engine_version", employer_payload.data)
        self.assertNotIn("candidate_email", employer_payload.data["assessment_context"])
        self.assertNotIn("candidate_passport_id", employer_payload.data["assessment_context"])
        self.assertNotIn("assessment_session_id", employer_payload.data["assessment_context"])
        self.assertNotIn("role_profile_version", employer_payload.data["assessment_context"])
        self.assertNotIn("email", employer_payload.data["candidate_snapshot"])
        self.assertNotIn("passport_id", employer_payload.data["candidate_snapshot"])
        self.assertNotIn("internal_reason", employer_payload.data["executive_summary"]["readiness_reason"])
        self.assertNotIn("top_source", employer_payload.data["executive_summary"])
        self.assertNotIn("face_match_score", employer_payload.data["identity_verification"])
        self.assertNotIn("liveness_passed", employer_payload.data["identity_verification"])
        self.assertEqual(employer_payload.data["identity_verification"]["employer_status"], "Not Completed")

        export_pdf = self.client.get(f"/api/v1/evaluations/reports/{report.public_id}/export-pdf")
        self.assertEqual(export_pdf.status_code, 200)
        self.assertEqual(export_pdf["Content-Type"], "application/pdf")

        verify_response = self.client.get(f"/api/v1/evaluations/reports/verify/{report.report_number}")
        self.assertEqual(verify_response.status_code, 200)
        self.assertEqual(verify_response.json()["verification_status"], "Authentic")
        self.assertEqual(verify_response.json()["public_report_status"], "Active")
        self.assertEqual(verify_response.json()["sha256_hash"], report.pdf_hash)
        self.assertNotIn("rule_engine_version", verify_response.json())

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

    def test_export_pdf_rebuilds_when_stored_file_is_missing(self):
        self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )
        report = EvaluationReport.objects.get(evaluation=self.evaluation, report_status=EvaluationReport.STATUS_ACTIVE)
        self.assertTrue(report.employer_pdf.storage.exists(report.employer_pdf.name))

        report.employer_pdf.storage.delete(report.employer_pdf.name)
        self.assertFalse(report.employer_pdf.storage.exists(report.employer_pdf.name))

        response = self.client.get(f"/api/v1/evaluations/reports/{report.public_id}/export-pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(f'{report.report_number}.pdf', response["Content-Disposition"])
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))

    def test_transcript_issue_text_is_normalized_for_employer_outputs(self):
        interpretation = CandidateResponseInterpretation.objects.get(response=self.response)
        interpretation.structured_output = {
            "transcript_issues": "T,h,e, ,r,e,s,p,o,n,s,e, ,n,e,e,d,s, ,r,e,v,i,e,w,.",
        }
        interpretation.save(update_fields=["structured_output"])

        self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )
        report = EvaluationReport.objects.get(evaluation=self.evaluation, report_status=EvaluationReport.STATUS_ACTIVE)

        transcript_flags = [
            flag["message"]
            for flag in report.human_review_flags
            if flag["flag_type"] == "transcript_issue"
        ]
        self.assertEqual(transcript_flags[0], "The response needs review.")
        self.assertIn(
            "The response needs review.",
            report.report_payload["risk_indicators"]["integrity_risk"]["evidence"],
        )

    def test_incomplete_assessment_hides_overall_score_from_employer_view(self):
        summary = SessionEvaluationSummary.objects.get(evaluation=self.evaluation, rule_set=self.rule_set)
        summary.evaluated_response_count = 0
        summary.total_response_count = 1
        summary.save(update_fields=["evaluated_response_count", "total_response_count"])

        response = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["report_payload"]["executive_summary"]["overall_score_display"],
            "Not fully available",
        )
        self.assertFalse(response.data["report_payload"]["executive_summary"]["overall_score_available"])

    def test_evidence_summary_uses_employer_friendly_grammar(self):
        result = ResponseEvaluationResult.objects.get(evaluation=self.evaluation, rule_set=self.rule_set)
        result.matched_indicators = ["dry floor"]
        result.observed_indicators = ["dry floor"]
        result.missing_indicators = ["identify hazard"]
        result.save(update_fields=["matched_indicators", "observed_indicators", "missing_indicators"])

        response = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        findings = [item["finding"] for item in response.data["report_payload"]["evidence_summary"]]
        self.assertTrue(any("Additional evidence is needed to identify hazards." in finding for finding in findings))
        self.assertFalse(any("for identify hazard" in finding for finding in findings))

    def test_employer_text_and_strengths_do_not_contradict_reported_risks(self):
        self.assertEqual(
            EvaluationReportService._friendly_competency_name("unmapped", "Unmapped"),
            "Overall Workforce Readiness",
        )
        self.assertEqual(
            EvaluationReportService._normalize_text("Response interpretation requires manual review.,"),
            "Response interpretation requires manual review.",
        )
        self.assertEqual(
            EvaluationReportService._normalize_text("Additional evidence is needed for identify hazard."),
            "Additional evidence is needed to identify hazards.",
        )

        strengths, risks = EvaluationReportService._derive_top_strengths_and_risks(
            competency_breakdown=[
                {
                    "display_name": "Patient Safety Awareness",
                    "percentage": 35,
                    "pass_threshold": 70,
                    "max_score": 10,
                    "completed_response_count": 1,
                    "assessment_status": "BELOW_THRESHOLD",
                }
            ],
            critical_failures=[],
        )

        self.assertEqual(strengths, ["No significant strengths identified in this assessment."])
        self.assertEqual(risks, ["Readiness gap identified in patient safety awareness"])

    def test_critical_competency_status_always_includes_all_five_dimensions(self):
        """Regression test: Practical Tasks was structurally excluded from
        this hardcoded category list, so it could never appear in the
        Competency Overview even when described elsewhere in the report."""
        status = EvaluationReportService._build_critical_competency_status(
            competency_breakdown=[],
            risk_indicators={},
        )
        labels = [item["label"] for item in status]
        self.assertEqual(
            labels,
            ["Safety", "Hygiene", "Communication", "Practical Tasks", "Behavioral Indicators"],
        )

    def test_assessment_coverage_reflects_real_per_dimension_status(self):
        # Regression test: the Assessment Coverage panel used to be a fixed
        # 5-item list, always rendered with a green checkmark, regardless of
        # whether a dimension actually had any evidence - contradicting the
        # same dimension's "Not Assessed" status shown elsewhere.
        status = [
            {"label": "Safety", "tone": "good"},
            {"label": "Hygiene", "tone": "good"},
            {"label": "Communication", "tone": "good"},
            {"label": "Practical Tasks", "tone": "good"},
            {"label": "Behavioral Indicators", "tone": "neutral"},
        ]
        coverage = EvaluationReportService._derive_assessment_coverage(status)
        self.assertEqual(
            coverage,
            [
                {"label": "Safety", "covered": True},
                {"label": "Hygiene", "covered": True},
                {"label": "Communication", "covered": True},
                {"label": "Practical Tasks", "covered": True},
                {"label": "Behavioral Indicators", "covered": False},
            ],
        )

    def test_competency_coverage_counts_only_dimensions_with_evidence(self):
        status = [
            {"label": "Safety", "tone": "good"},
            {"label": "Hygiene", "tone": "neutral"},
            {"label": "Communication", "tone": "good"},
            {"label": "Practical Tasks", "tone": "warn"},
            {"label": "Integrity", "tone": "neutral"},
        ]
        self.assertEqual(EvaluationReportService._derive_competency_coverage(status), 3)

    def test_empty_competency_scores_are_reported_as_not_assessed(self):
        risks = EvaluationReportService._build_risk_indicators(
            competency_breakdown=[],
            response_evidence_summary=[],
            human_review_flags=[],
            critical_failures=[],
        )
        status = EvaluationReportService._build_critical_competency_status(
            competency_breakdown=[],
            risk_indicators=risks,
        )

        self.assertEqual(risks["hygiene_risk"]["risk_score_display"], "Not Assessed")
        self.assertEqual(risks["communication_risk"]["level"], "Not Assessed")
        self.assertEqual(status[1]["score_display"], "Not Assessed")
        self.assertEqual(status[2]["status_label"], "Not Assessed")

    def test_multi_sentence_evidence_does_not_produce_run_on_punctuation(self):
        risk_indicators = {
            "integrity_risk": {
                "level": "Medium",
                "risk_score": 40,
                "evidence": [
                    "Response interpretation requires manual review.",
                    "The response is unclear and does not relate to patient confidentiality or response protocol.",
                    "Insufficient detail to provide a comprehensive answer.",
                ],
            },
            "note": "unused",
        }
        status = EvaluationReportService._build_critical_competency_status(
            competency_breakdown=[],
            risk_indicators=risk_indicators,
        )
        integrity_summary = next(item for item in status if item["label"] == "Behavioral Indicators")["summary"]

        self.assertNotIn(".,", integrity_summary)
        self.assertEqual(
            integrity_summary,
            "Response interpretation requires manual review. The response is unclear and does not relate to "
            "patient confidentiality or response protocol. Insufficient detail to provide a comprehensive answer.",
        )

    def test_assessment_quality_requires_real_evidence_for_excellent(self):
        # Regression test: "Excellent" quality was reachable with zero real
        # evidence, because avg_stt silently defaulted to a passing 0.9 when
        # there were no STT-confidence scores to average - completeness and
        # duration alone were enough, with no check that any evidence exists.
        from types import SimpleNamespace
        self.session.started_at = timezone.now() - timezone.timedelta(minutes=45)
        self.session.ended_at = timezone.now()
        self.session.save(update_fields=["started_at", "ended_at"])
        summary = SimpleNamespace(total_response_count=1, evaluated_response_count=1)

        empty_evidence_quality = EvaluationReportService._derive_assessment_quality(
            session=self.session,
            summary=summary,
            human_review_flags=[],
            response_evidence_summary=[],
        )
        self.assertNotIn(empty_evidence_quality, ("Excellent", "Good"))

        real_evidence_quality = EvaluationReportService._derive_assessment_quality(
            session=self.session,
            summary=summary,
            human_review_flags=[],
            response_evidence_summary=[
                {"traceability": {"transcript_reference": {"confidence": "0.95"}}},
            ],
        )
        self.assertEqual(real_evidence_quality, "Excellent")

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

    def test_report_verify_reflects_stale_status_live(self):
        first = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )
        first_report = EvaluationReport.objects.get(public_id=first.data["id"])

        before = self.client.get(f"/api/v1/evaluations/reports/verify/{first_report.report_number}")
        self.assertEqual(before.json()["verification_status"], "Authentic")

        self.client.post(f"/api/v1/evaluations/reports/{first_report.public_id}/regenerate", {}, format="json")

        after = self.client.get(f"/api/v1/evaluations/reports/verify/{first_report.report_number}")
        self.assertEqual(after.json()["verification_status"], "Superseded")

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

    def test_generate_report_fails_when_consent_not_signed(self):
        self.session.candidate_consent_agreement = None
        self.session.save(update_fields=["candidate_consent_agreement"])

        response = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("consent", response.data["detail"].lower())
        self.assertFalse(EvaluationReport.objects.filter(evaluation=self.evaluation).exists())

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
        new_consent_agreement = Agreement.objects.create(
            user=self.user,
            agreement_type=AgreementType.CANDIDATE_CONSENT,
            version="v1",
            method=AgreementMethod.CHECKBOX,
            status=AgreementStatus.SIGNED,
            accepted_at=timezone.now(),
        )
        new_session.status = "COMPLETED"
        new_session.ended_at = timezone.now()
        new_session.candidate_consent_agreement = new_consent_agreement
        new_session.save(update_fields=["status", "ended_at", "updated_at", "candidate_consent_agreement"])

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

    def _add_full_competency_coverage(self):
        """Adds hygiene/communication/practical-task evidence on top of the
        single safety_awareness question setUp already builds, so the
        fixture clears MINIMUM_ASSESSED_COMPETENCIES (4) and can reach
        overall_score_available=True - required to exercise the
        scheduled-interview authoritative-score logic, which only kicks in
        once a headline score would otherwise be computable."""
        competencies = [
            ("hygiene_standards", "Hygiene Standards", "HK-HYG-001",
             ["clear surface", "apply sanitizer", "wipe dry"]),
            ("communication_ability", "Communication Ability", "HK-COM-001",
             ["listen first", "restate clearly", "confirm understanding"]),
            ("practical_task_execution", "Practical Task Execution", "HK-TASK-001",
             ["set temperature", "iron collar first", "hang immediately"]),
        ]
        for i, (code, name, question_code, indicators) in enumerate(competencies, start=2):
            template = QuestionTemplate.objects.create(
                role_name="Housekeeper", role_code="domestic_worker", question_code=question_code,
                question_version="1.0", question_status=QuestionLifecycleStatus.ACTIVE,
                domain="Household Operations", skill_tag=code, skill=name, sequence_number=i,
                difficulty=QuestionDifficulty.MEDIUM, question_text=f"Describe {name}.",
                question_type="scenario", question_format="SCENARIO", language="EN",
                scoring_type="0/3/5", difficulty_score=2, estimated_time_seconds=60,
                expected_answer_type="multi_step", evaluation_tier=InterviewEvaluationTier.FULL,
                rubric_version="v2.0", question_set_version="v1.2", critical_question=False, is_active=True,
            )
            session_question = SessionQuestion.objects.create(
                session=self.session, question_template=template, question_text=template.question_text,
                domain=template.domain, skill=template.skill_tag, difficulty=template.difficulty,
                question_order=i, status="ANSWERED", is_mandatory=True,
                asked_at=timezone.now(), answered_at=timezone.now(),
            )
            answer_text = f"I would {', '.join(indicators)}."
            response = CandidateResponse.objects.create(
                session=self.session, question=session_question, response_type=CandidateResponseType.TEXT,
                transcript=answer_text, original_transcript=answer_text, transcript_language="en",
                translated_transcript=answer_text, translation_status="COMPLETED", text_response=answer_text,
                interpretation_status="COMPLETED", processing_status="RULE_INPUT_PREPARED",
                stt_status="COMPLETED", stt_confidence="0.9500",
            )
            CandidateResponseTranslation.objects.create(
                response=response, session=self.session, question=session_question,
                source_language="EN", target_language="EN", original_transcript=answer_text,
                translated_transcript=answer_text, provider="fake-translate", provider_model="v1",
                status="COMPLETED",
            )
            CandidateResponseInterpretation.objects.create(
                response=response, session=self.session, question=session_question,
                provider="fake-interpret", model="v1", status="COMPLETED", confidence_score="0.950",
                structured_output={"transcript_issues": []},
            )
            EvaluationInputArtifact.objects.create(
                response=response, session=self.session, question=session_question,
                competency_code=code, expected_indicators=indicators, observed_indicators=indicators,
                missing_indicators=[], risk_flags=[], source_interpretation_status="COMPLETED",
                requires_human_review=False,
            )
            ScoringRule.objects.create(
                rule_set=self.rule_set, competency_code=code, competency_name=name,
                question_template=template, question_code=question_code, expected_indicators=indicators,
                required_indicators=indicators[:1], weighted_indicators={ind: "3" for ind in indicators},
                max_score="10.00", pass_threshold="7.00",
                scoring_method=ScoringRule.SCORING_METHOD_WEIGHTED_MATCH, is_active=True,
            )
        Week6ScoringService.run_for_evaluation(evaluation=self.evaluation, actor=self.user, rule_set=self.rule_set)

    def test_scheduled_interview_report_shows_ai_score_as_secondary_when_rating_pending(self):
        self._add_full_competency_coverage()
        self.session.scheduled_start_at = timezone.now() - timezone.timedelta(days=1)
        self.session.save(update_fields=["scheduled_start_at"])

        response = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        summary = response.data["report_payload"]["executive_summary"]
        self.assertIsNone(summary["overall_score"])
        self.assertFalse(summary["overall_score_available"])
        self.assertEqual(summary["overall_score_unavailable_reason"], "EVALUATOR_RATING_PENDING")
        self.assertEqual(summary["overall_score_display"], "Pending Evaluator Rating")
        self.assertEqual(summary["score_source"], None)
        self.assertIsNotNone(summary["secondary_score"])
        self.assertEqual(summary["secondary_score"]["source"], "AI_ASSESSMENT")

    def test_scheduled_interview_report_uses_evaluator_average_once_rated(self):
        self._add_full_competency_coverage()
        self.session.scheduled_start_at = timezone.now() - timezone.timedelta(days=1)
        self.session.save(update_fields=["scheduled_start_at"])
        EvaluatorRating.objects.create(
            evaluation=self.evaluation,
            safety_awareness=80, hygiene=75, communication=85, behavior_integrity=70, task_execution=60,
            rated_by=self.user,
        )

        response = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        summary = response.data["report_payload"]["executive_summary"]
        # Average of the 5 approved dimensions only - consistency is a
        # reliability metric, not a competency, and is excluded.
        expected = round((80 + 75 + 85 + 70 + 60) / 5)
        self.assertEqual(summary["overall_score"], expected)
        self.assertTrue(summary["overall_score_available"])
        self.assertEqual(summary["score_source"], "EVALUATOR_ASSESSMENT")
        self.assertIsNotNone(summary["secondary_score"])
        self.assertEqual(summary["secondary_score"]["source"], "AI_ASSESSMENT")

    def test_ai_interview_report_score_source_unchanged(self):
        response = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        summary = response.data["report_payload"]["executive_summary"]
        self.assertEqual(summary["score_source"], "AI_ASSESSMENT")
        self.assertIsNone(summary["secondary_score"])
        self.assertEqual(summary["overall_score_display"], "Not fully available")
        self.assertFalse(summary["overall_score_available"])

    def test_assessment_mode_reflects_real_session_mode(self):
        default_response = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )
        self.assertEqual(
            default_response.data["report_payload"]["assessment_context"]["assessment_mode"],
            "Guided Digital Simulation",
        )

        self.session.scheduled_start_at = timezone.now() - timezone.timedelta(days=1)
        self.session.save(update_fields=["scheduled_start_at"])
        scheduled_response = self.client.post(
            f"/api/v1/evaluations/evaluations/{self.evaluation.public_id}/generate-report",
            {},
            format="json",
        )
        self.assertEqual(
            scheduled_response.data["report_payload"]["assessment_context"]["assessment_mode"],
            "Scheduled Interview (Evaluator-Conducted)",
        )


class TopStrengthsAndRisksTests(TestCase):
    """A competency can never appear as both a "strength" and a risk/gap in
    the same report - a strength must have actually met its own configured
    pass_threshold, not just ranked highest among several failing
    competencies."""

    def test_highest_scoring_competency_is_not_a_strength_if_it_failed_its_own_threshold(self):
        competency_breakdown = [
            {
                "display_name": "Patient Safety Awareness",
                "percentage": 40,
                "pass_threshold": 70,
                "max_score": 10,
                "completed_response_count": 2,
                "assessment_status": "BELOW_THRESHOLD",
            },
            {
                "display_name": "Hygiene Standards",
                "percentage": 10,
                "pass_threshold": 70,
                "max_score": 10,
                "completed_response_count": 2,
                "assessment_status": "BELOW_THRESHOLD",
            },
        ]

        strengths, risks = EvaluationReportService._derive_top_strengths_and_risks(
            competency_breakdown=competency_breakdown,
            critical_failures=[],
        )

        self.assertEqual(strengths, ["No significant strengths identified in this assessment."])
        self.assertIn("Readiness gap identified in patient safety awareness", risks)

    def test_a_competency_that_actually_passed_its_threshold_is_a_strength(self):
        competency_breakdown = [
            {
                "display_name": "Patient Safety Awareness",
                "percentage": 90,
                "pass_threshold": 70,
                "max_score": 10,
                "completed_response_count": 2,
                "assessment_status": "MEETS_THRESHOLD",
            },
            {
                "display_name": "Hygiene Standards",
                "percentage": 10,
                "pass_threshold": 70,
                "max_score": 10,
                "completed_response_count": 2,
                "assessment_status": "BELOW_THRESHOLD",
            },
        ]

        strengths, risks = EvaluationReportService._derive_top_strengths_and_risks(
            competency_breakdown=competency_breakdown,
            critical_failures=[],
        )

        self.assertIn("Strong patient safety awareness", strengths)
        self.assertNotIn("Readiness gap identified in patient safety awareness", risks)
