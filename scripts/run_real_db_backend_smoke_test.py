#!/usr/bin/env python3
import os
import sys
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meritlense.settings")

import django  # noqa: E402

django.setup()

from django.core.files.base import ContentFile  # noqa: E402
from django.db import transaction  # noqa: E402
from django.db.migrations.executor import MigrationExecutor  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIRequestFactory, APIClient, force_authenticate  # noqa: E402

from api.accounts.models import Company, CompanyEmployerProfile, User  # noqa: E402
from api.candidates.models import Candidate  # noqa: E402
from api.core.constants import (  # noqa: E402
    CandidateResponseType,
    EvaluationType,
    InterviewEvaluationTier,
    QuestionDifficulty,
    QuestionLifecycleStatus,
    Roles,
)
from api.evaluations.models import Evaluation, ResponseEvaluationResult, ScoringRule, ScoringRuleSet  # noqa: E402
from api.evaluations.scoring_services import Week6ScoringService  # noqa: E402
from api.interviews.models import InterviewConfiguration  # noqa: E402
from api.evaluations.views import ScoringRuleSetViewSet  # noqa: E402
from api.questions.models import QuestionTemplate  # noqa: E402
from api.sessions.models import CandidateResponse, SessionQuestion  # noqa: E402
from api.sessions.services import InterviewSessionService, InterviewVoicePipelineService  # noqa: E402
from api.translation.models import CandidateResponseInterpretation, CandidateResponseTranslation, EvaluationInputArtifact  # noqa: E402


class SmokeTestFailure(Exception):
    pass


def headline(text):
    print(f"\n== {text} ==")


def ok(text):
    print(f"[PASS] {text}")


def fail(text):
    raise SmokeTestFailure(text)


def assert_true(condition, message):
    if not condition:
        fail(message)


def unique(label):
    return f"smoke-{label}-{uuid.uuid4().hex[:8]}"


def ensure_no_pending_migrations():
    executor = MigrationExecutor(transaction.get_connection())
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    assert_true(not plan, "Pending migrations exist on the target database. Run migrate first.")
    ok("No pending migrations on target database")


def build_fake_translation_provider():
    class FakeTranslationProvider:
        def translate(self, **kwargs):
            return {
                "provider": "GOOGLE",
                "provider_model": "fake-translate",
                "source_language": kwargs.get("source_language", "AR"),
                "target_language": kwargs.get("target_language", "EN"),
                "translated_text": "Keep the child away, then clean the spill.",
                "metadata": {"request_id": unique("translate")},
            }

    return FakeTranslationProvider


def build_fake_interpretation_provider():
    class FakeInterpretationProvider:
        def interpret(self, **kwargs):
            return {
                "provider": "OPENAI",
                "model": "fake-gpt",
                "raw_content": (
                    '{"answer_relevance":"high","mentioned_steps":["keep child away","clean spill"],'
                    '"missing_steps":["prevent recurrence"],"safety_risks":[],"compliance_risks":[],'
                    '"language_quality":"clear","extraction_confidence":0.91,"confidence_notes":[],'
                    '"uncertainty_notes":[],"transcript_issues":[],"key_evidence_phrases":["keep child away"]}'
                ),
                "metadata": {"request_id": unique("interpret")},
            }

    return FakeInterpretationProvider()


def build_fake_stt_service():
    class FakeSttService:
        provider = "OPENAI"

        def transcribe(self, **kwargs):
            return {
                "provider": "OPENAI",
                "provider_model": "fake-whisper",
                "request_id": unique("stt"),
                "detected_language": "en",
                "confidence": None,
                "processing_status": "COMPLETED",
                "transcript": "I would keep the child safe first.",
                "metadata": {"segments": []},
            }

    return FakeSttService


def create_b2c_candidate(role_code="nanny", preferred_language="EN"):
    user = User.objects.create_user(
        email=f"{unique('user')}@example.com",
        password="testpass123",
        first_name="Smoke",
        last_name="Owner",
        role=Roles.B2C,
        is_verified=True,
    )
    candidate = Candidate.objects.create(
        first_name="Smoke",
        last_name="Candidate",
        email=f"{unique('candidate')}@example.com",
        passport_id=unique("passport"),
        job_role="NA",
        core_skills="care,safety",
        preferred_language=preferred_language,
        passport_document="candidates/documents/passport/test.pdf",
        created_by=user,
    )
    config = InterviewConfiguration.objects.create(
        role_name="Nanny",
        role_code=role_code,
        language="EN",
        evaluation_tier=InterviewEvaluationTier.FULL,
        duration_minutes=30,
        total_questions=1,
        allow_retries=True,
        max_retries=1,
        enable_translation=True,
        rubric_version="v1",
        question_set_version="v1",
    )
    QuestionTemplate.objects.create(
        role_name="Nanny",
        role_code=role_code,
        question_code=unique("qt"),
        question_version="1.0",
        question_status=QuestionLifecycleStatus.ACTIVE,
        domain="Child Care",
        skill_tag="Safety Awareness",
        skill="Safety Awareness",
        sequence_number=1,
        difficulty=QuestionDifficulty.MEDIUM,
        question_text="What would you do if a child is near a spill?",
        question_type="knowledge",
        question_format="TEXT",
        expected_steps=["keep child away", "clean spill", "prevent recurrence"],
        keywords=["child", "spill", "safety"],
        language="EN",
        scoring_type="0/3/5",
        difficulty_score=2,
        estimated_time_seconds=45,
        expected_answer_type="structured",
        evaluation_tier=InterviewEvaluationTier.FULL,
        rubric_version="v1",
        question_set_version="v1",
        critical_question=True,
        is_active=True,
    )
    return user, candidate, config


def test_text_response_auto_processing():
    headline("Text Response Auto Processing")
    user, candidate, config = create_b2c_candidate(preferred_language="AR")
    session = InterviewSessionService.create_session(candidate=candidate, config=config, created_by=user)
    session.translation_target = "EN"
    session.save(update_fields=["translation_target", "updated_at"])
    InterviewSessionService.start_session(session, actor=user)
    question, completed = InterviewSessionService.get_or_activate_current_question(session, actor=user)
    assert_true(not completed and question is not None, "Session question was not activated")

    with ExitStack() as stack:
        stack.enter_context(
            override_settings(
                ENABLE_ASYNC_AI_PROCESSING=False,
                ENABLE_TRANSLATION_PIPELINE=True,
                ENABLE_RESPONSE_INTERPRETATION=True,
                ENABLE_RULE_INPUT_PREPARATION=True,
                GOOGLE_TRANSLATE_API_KEY="smoke-key",
                OPENAI_API_KEY="smoke-key",
            )
        )
        stack.enter_context(patch("api.translation.services.TranslationService.provider_class", build_fake_translation_provider()))
        stack.enter_context(
            patch("api.translation.services.ResponseInterpretationService.get_provider", return_value=build_fake_interpretation_provider())
        )
        response = InterviewSessionService.submit_response(
            session,
            question,
            actor=user,
            transcript="ابق الطفل بعيدا ثم نظف السائل",
            text_response="ابق الطفل بعيدا ثم نظف السائل",
        )

    response.refresh_from_db()
    translation = CandidateResponseTranslation.objects.get(response=response)
    interpretation = CandidateResponseInterpretation.objects.get(response=response)
    artifact = EvaluationInputArtifact.objects.get(response=response)
    assert_true(response.processing_status == "PROCESSING_COMPLETED", "Text response AI processing did not complete")
    assert_true(response.translation_status == "COMPLETED", "Text response translation did not complete")
    assert_true(response.interpretation_status == "COMPLETED", "Text response interpretation did not complete")
    assert_true(translation.translated_transcript == "Keep the child away, then clean the spill.", "Translated transcript mismatch")
    assert_true(interpretation.status == "COMPLETED", "Interpretation artifact missing")
    assert_true(bool(artifact.observed_indicators), "Evaluation input artifact was not created")
    ok("Text submit auto-triggered translation, interpretation, and evaluation input artifact creation")


def test_voice_transcription_auto_processing():
    headline("Voice Transcription Auto Processing")
    user, candidate, config = create_b2c_candidate(preferred_language="EN")
    session = InterviewSessionService.create_session(candidate=candidate, config=config, created_by=user)
    session.translation_target = ""
    session.save(update_fields=["translation_target", "updated_at"])
    InterviewSessionService.start_session(session, actor=user)
    question, completed = InterviewSessionService.get_or_activate_current_question(session, actor=user)
    assert_true(not completed and question is not None, "Session question was not activated")

    response = CandidateResponse.objects.create(
        session=session,
        question=question,
        response_type=CandidateResponseType.VOICE,
        attempt_number=1,
    )
    response.audio_file.save("smoke-answer.webm", ContentFile(b"voice-bytes"), save=True)
    response.audio_mime_type = "audio/webm"
    response.audio_uploaded_at = timezone.now()
    response.save(update_fields=["audio_file", "audio_mime_type", "audio_uploaded_at", "updated_at"])

    with ExitStack() as stack:
        stack.enter_context(
            override_settings(
                ENABLE_ASYNC_AI_PROCESSING=False,
                ENABLE_TRANSLATION_PIPELINE=True,
                ENABLE_RESPONSE_INTERPRETATION=True,
                ENABLE_RULE_INPUT_PREPARATION=True,
                OPENAI_API_KEY="smoke-key",
            )
        )
        stack.enter_context(
            patch("api.sessions.services.InterviewVoicePipelineService.stt_service_class", build_fake_stt_service())
        )
        stack.enter_context(
            patch("api.translation.services.ResponseInterpretationService.get_provider", return_value=build_fake_interpretation_provider())
        )
        InterviewVoicePipelineService.transcribe_response(session=session, response=response, actor=user)

    response.refresh_from_db()
    interpretation = CandidateResponseInterpretation.objects.get(response=response)
    artifact = EvaluationInputArtifact.objects.get(response=response)
    assert_true(response.stt_status == "COMPLETED", "STT did not complete")
    assert_true(response.processing_status == "PROCESSING_COMPLETED", "Voice response AI processing did not complete")
    assert_true(response.translation_status == "NOT_REQUIRED", "Voice response should not require translation")
    assert_true(interpretation.status == "COMPLETED", "Voice interpretation artifact missing")
    assert_true(bool(artifact.observed_indicators), "Voice evaluation input artifact missing")
    ok("Voice transcription auto-triggered interpretation and evaluation input artifact creation")


def test_critical_failure_raw_score_preserved():
    headline("Critical Failure Score Preservation")
    user, candidate, config = create_b2c_candidate(preferred_language="EN")
    session = InterviewSessionService.create_session(candidate=candidate, config=config, created_by=user)
    question = session.questions.first()
    question.status = "ANSWERED"
    question.asked_at = timezone.now()
    question.answered_at = timezone.now()
    question.save(update_fields=["status", "asked_at", "answered_at", "updated_at"])

    response = CandidateResponse.objects.create(
        session=session,
        question=question,
        response_type=CandidateResponseType.TEXT,
        transcript="I would identify the hazard and clean the spill.",
        original_transcript="I would identify the hazard and clean the spill.",
        transcript_language="en",
        translation_status="NOT_REQUIRED",
        interpretation_status="COMPLETED",
        processing_status="PROCESSING_COMPLETED",
    )
    EvaluationInputArtifact.objects.create(
        response=response,
        session=session,
        question=question,
        competency_code="safety_awareness",
        expected_indicators=["identify hazard", "clean spill", "prevent recurrence"],
        observed_indicators=["identify hazard", "clean spill"],
        missing_indicators=["prevent recurrence"],
        risk_flags=[],
        source_interpretation_status="COMPLETED",
    )
    evaluation, _ = Evaluation.objects.get_or_create(
        session=session,
        defaults={
            "candidate": candidate,
            "evaluation_type": EvaluationType.INTERVIEW,
            "scheduled_date": timezone.now(),
            "duration_minutes": 45,
            "created_by": user,
            "company": candidate.company,
            "evaluation_tier": InterviewEvaluationTier.FULL,
        },
    )
    rule_set = ScoringRuleSet.objects.create(
        name=unique("rules"),
        version="v1",
        role_code=config.role_code,
        role_name=config.role_name,
        evaluation_tier=InterviewEvaluationTier.FULL,
        is_active=True,
        created_by=user,
        company=candidate.company,
    )
    ScoringRule.objects.create(
        rule_set=rule_set,
        competency_code="safety_awareness",
        competency_name="Safety Awareness",
        question_template=question.question_template,
        question_code=question.question_template.question_code,
        expected_indicators=["identify hazard", "clean spill", "prevent recurrence"],
        required_indicators=["identify hazard"],
        weighted_indicators={"identify hazard": "4", "clean spill": "3", "prevent recurrence": "3"},
        critical_failure_indicators=["clean spill"],
        max_score="10.00",
        pass_threshold="7.00",
        scoring_method=ScoringRule.SCORING_METHOD_WEIGHTED_MATCH,
        is_active=True,
    )

    summary = Week6ScoringService.run_for_evaluation(evaluation=evaluation, actor=user, rule_set=rule_set)
    result = ResponseEvaluationResult.objects.get(response=response, rule_set=rule_set)
    assert_true(result.critical_failure is True, "Critical failure flag was not preserved")
    assert_true(str(result.score) == "7.00", "Raw score was not preserved")
    assert_true(result.metadata.get("effective_score") == "0.00", "Effective score metadata missing")
    assert_true(summary.critical_failures, "Summary critical failure payload missing")
    ok("Critical failure preserves raw score while still triggering readiness override payload")


def test_scoring_ruleset_tenant_scoping():
    headline("Scoring Rule Set Tenant Scoping")
    owner_a = User.objects.create_user(
        email=f"{unique('b2b-a')}@example.com",
        password="testpass123",
        first_name="Tenant",
        last_name="A",
        role=Roles.B2B,
        is_verified=True,
    )
    owner_b = User.objects.create_user(
        email=f"{unique('b2b-b')}@example.com",
        password="testpass123",
        first_name="Tenant",
        last_name="B",
        role=Roles.B2B,
        is_verified=True,
    )
    company_a = Company.objects.create(
        name=unique("company-a"),
        registration_number=unique("reg-a"),
        company_size="11-50",
        industry="Care",
        phone_number="+251900000001",
        country="Ethiopia",
        city="Addis Ababa",
        admin_user=owner_a,
        registration_certificate="companies/certificates/a.pdf",
    )
    company_b = Company.objects.create(
        name=unique("company-b"),
        registration_number=unique("reg-b"),
        company_size="11-50",
        industry="Care",
        phone_number="+251900000002",
        country="Ethiopia",
        city="Addis Ababa",
        admin_user=owner_b,
        registration_certificate="companies/certificates/b.pdf",
    )
    CompanyEmployerProfile.objects.create(
        user=owner_a,
        company_name=company_a.name,
        company_registration_number=company_a.registration_number,
        company_size=company_a.company_size,
        company=company_a,
    )
    CompanyEmployerProfile.objects.create(
        user=owner_b,
        company_name=company_b.name,
        company_registration_number=company_b.registration_number,
        company_size=company_b.company_size,
        company=company_b,
    )
    own_rule_set = ScoringRuleSet.objects.create(
        name=unique("tenant-rules-a"),
        version="v1",
        role_code="nanny",
        role_name="Nanny",
        evaluation_tier=InterviewEvaluationTier.FULL,
        is_active=True,
        created_by=owner_a,
        company=company_a,
    )
    ScoringRuleSet.objects.create(
        name=unique("tenant-rules-b"),
        version="v1",
        role_code="nanny",
        role_name="Nanny",
        evaluation_tier=InterviewEvaluationTier.FULL,
        is_active=True,
        created_by=owner_b,
        company=company_b,
    )

    factory = APIRequestFactory()
    list_view = ScoringRuleSetViewSet.as_view({"get": "list"})
    detail_view = ScoringRuleSetViewSet.as_view({"get": "retrieve"})
    create_view = ScoringRuleSetViewSet.as_view({"post": "create"})

    list_request = factory.get("/api/v1/evaluations/rule-sets")
    force_authenticate(list_request, user=owner_a)
    list_response = list_view(list_request)

    detail_request = factory.get(f"/api/v1/evaluations/rule-sets/{own_rule_set.public_id}")
    force_authenticate(detail_request, user=owner_a)
    detail_response = detail_view(detail_request, id=str(own_rule_set.public_id))

    create_request = factory.post(
        "/api/v1/evaluations/rule-sets",
        {
            "name": unique("tenant-rules-created"),
            "version": "v2",
            "role_code": "nanny",
            "role_name": "Nanny",
            "evaluation_tier": InterviewEvaluationTier.FULL,
            "is_active": True,
            "rules": [],
        },
        format="json",
    )
    force_authenticate(create_request, user=owner_a)
    create_response = create_view(create_request)

    cross_request = factory.get(f"/api/v1/evaluations/rule-sets/{own_rule_set.public_id}")
    force_authenticate(cross_request, user=owner_b)
    cross_response = detail_view(cross_request, id=str(own_rule_set.public_id))

    assert_true(list_response.status_code == 200, "Tenant list endpoint failed")
    assert_true(len(list_response.data) == 1, "Tenant list endpoint leaked other company rule sets")
    assert_true(detail_response.status_code == 200, "Owner could not fetch own company rule set")
    assert_true(cross_response.status_code == 404, "Cross-company rule set access was not blocked")
    assert_true(create_response.status_code == 201, "Tenant rule set creation failed")
    created = ScoringRuleSet.objects.get(public_id=create_response.data["id"])
    assert_true(created.company_id == company_a.id, "Created tenant rule set was not auto-scoped to company")
    ok("Scoring rule set API is tenant-scoped and auto-attaches company ownership")


def main():
    print("Running rollback-safe backend smoke test against the configured database.")
    print("All created data will be rolled back at the end of the run.")
    connection = transaction.get_connection()
    print(f"Database vendor: {connection.vendor}")
    print(f"Settings module: {os.environ.get('DJANGO_SETTINGS_MODULE')}")

    with transaction.atomic():
        ensure_no_pending_migrations()
        test_text_response_auto_processing()
        test_voice_transcription_auto_processing()
        test_critical_failure_raw_score_preserved()
        test_scoring_ruleset_tenant_scoping()
        transaction.set_rollback(True)

    print("\nSmoke test completed successfully. All database writes were rolled back.")


if __name__ == "__main__":
    try:
        main()
    except SmokeTestFailure as exc:
        print(f"\nSMOKE TEST FAILED: {exc}")
        sys.exit(1)
    except Exception as exc:  # pragma: no cover
        print(f"\nUNEXPECTED ERROR: {exc}")
        raise
