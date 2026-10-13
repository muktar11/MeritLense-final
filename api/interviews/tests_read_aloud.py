"""Read-aloud (text-to-speech) language selection for interview questions.

Arabic sessions ask the approved Arabic templates. The voice must read the
approved text in the chosen language - not a machine translation of it, and
never Arabic text through an English voice (or the reverse)."""
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from api.accounts.models import Company, User
from api.candidates.models import Candidate
from api.core.constants import InterviewEvaluationTier, Roles
from api.interviews.models import InterviewConfiguration
from api.questions.models import QuestionTemplate
from api.sessions.models import InterviewSession
from api.sessions.services import InterviewVoicePipelineService, QuestionGenerationService
from api.interviews.question_language import TranslationUnavailable
from api.translation.services import AIProcessingError

VERSION = "GOV1.2-FINAL"


class ReadAloudLanguageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_governance_question_bank", verbosity=0)
        cls.owner = User.objects.create_user(email="ra@example.com", password="x", first_name="R", last_name="A",
                                             role=Roles.B2B, is_verified=True)
        cls.company = Company.objects.create(name="RA Co", registration_number="RA-1", company_size="11-50",
                                             phone_number="+1", country="X", city="Y", admin_user=cls.owner)

    def setUp(self):
        cache.clear()  # the display-translation "provider down" flag

    def _questions(self, lang):
        config = InterviewConfiguration.objects.get(role_code="nursing_assistant", language=lang,
                                                   evaluation_tier=InterviewEvaluationTier.SCREENING,
                                                   question_set_version=VERSION)
        cand = Candidate.objects.create(first_name="C", last_name=lang, email=f"ra-{lang}@example.com",
                                        passport_id=f"RA{lang}", job_role="OT", preferred_language=lang,
                                        passport_document="x.pdf", created_by=self.owner, company=self.company)
        session = InterviewSession.objects.create(
            candidate=cand, organization=self.company, config=config, role_name=config.role_name,
            role_code="nursing_assistant", ui_language=lang, candidate_language=lang,
            evaluation_tier=InterviewEvaluationTier.SCREENING, total_questions=config.total_questions,
            rubric_version=VERSION, question_set_version=VERSION, expires_at=InterviewSession.build_expiry(30),
            created_by=self.owner, started_at=timezone.now())
        return QuestionGenerationService.generate_questions(session)

    def _sibling(self, question, lang):
        return QuestionTemplate.objects.get(question_code=question.question_template.question_code,
                                            question_set_version=VERSION, language=lang, is_active=True)

    def _no_translation(self):
        return patch("api.interviews.question_language.TranslationService.translate",
                     side_effect=AssertionError("approved text must not be machine-translated"))

    def test_arabic_session_reads_the_approved_arabic_text_in_arabic(self):
        with self._no_translation():
            for q in self._questions("AR"):
                self.assertEqual(InterviewVoicePipelineService._read_aloud_text(q, "ar-SA"), q.question_text)

    def test_arabic_session_read_in_english_uses_the_approved_english_question(self):
        with self._no_translation():
            for q in self._questions("AR"):
                text = InterviewVoicePipelineService._read_aloud_text(q, "en-US")
                self.assertEqual(text, self._sibling(q, "EN").question_text)
                self.assertNotRegex(text, r"[؀-ۿ]")  # no Arabic sent to the English voice

    def test_english_session_read_in_arabic_uses_the_approved_arabic_question(self):
        with self._no_translation():
            for q in self._questions("EN"):
                self.assertEqual(InterviewVoicePipelineService._read_aloud_text(q, "ar-SA"),
                                 self._sibling(q, "AR").question_text)

    def test_other_languages_are_translated_from_the_questions_real_language(self):
        q = self._questions("AR")[0]
        with patch("api.interviews.question_language.TranslationService.translate",
                   return_value={"translated_text": "texto"}) as translate:
            self.assertEqual(InterviewVoicePipelineService._read_aloud_text(q, "es-ES"), "texto")
        translate.assert_called_once_with(text=q.question_text, source_language="ar", target_language="es")

    def test_translation_failure_tries_openai_then_refuses_instead_of_reading_wrong_language(self):
        q = self._questions("EN")[0]
        with patch("api.interviews.question_language.TranslationService.translate", side_effect=AIProcessingError("down")), \
                patch("api.interviews.question_language._openai_translate", return_value=("ጽሑፍ", "OPENAI:test")) as openai:
            self.assertEqual(InterviewVoicePipelineService._read_aloud_text(q, "am-ET"), "ጽሑፍ")
        openai.assert_called_once()

        q2 = self._questions("AR")[1]
        with patch("api.interviews.question_language.TranslationService.translate", side_effect=AIProcessingError("down")), \
                patch("api.interviews.question_language._openai_translate", side_effect=TranslationUnavailable("down")):
            with self.assertRaises(ValueError):
                InterviewVoicePipelineService._read_aloud_text(q2, "am-ET")


class QuestionTextEndpointTests(ReadAloudLanguageTests.__base__):
    """GET /interviews/{id}/question-text and /prechecks/statement: what the
    candidate sees in another language, beside the original question."""

    setUpTestData = ReadAloudLanguageTests.__dict__["setUpTestData"]
    setUp = ReadAloudLanguageTests.setUp
    _questions = ReadAloudLanguageTests._questions
    _sibling = ReadAloudLanguageTests._sibling

    def _get(self, question, path, **params):
        session = question.session
        return self.client.get(f"/api/v1/interviews/{session.public_id}/{path}/",
                               {"token": session.access_token, **params})

    def test_approved_sibling_for_the_other_source_language_without_translating(self):
        q = self._questions("EN")[0]
        with patch("api.interviews.question_language.TranslationService.translate",
                   side_effect=AssertionError("approved text must not be machine-translated")):
            response = self._get(q, "question-text", question_id=str(q.public_id), language="AR")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["kind"], "approved")
        self.assertEqual(response.data["text"], self._sibling(q, "AR").question_text)

        same = self._get(q, "question-text", question_id=str(q.public_id), language="en-US")
        self.assertEqual(same.data, {"language": "EN", "text": q.question_text, "kind": "original", "provider": ""})

    def test_other_language_is_translated_once_and_stored_on_the_question(self):
        q = self._questions("EN")[0]
        with patch("api.interviews.question_language.TranslationService.translate",
                   return_value={"translated_text": "የተተረጎመ", "provider": "GOOGLE"}) as translate:
            first = self._get(q, "question-text", question_id=str(q.public_id), language="am-ET")
            second = self._get(q, "question-text", question_id=str(q.public_id), language="AM")
        self.assertEqual(first.data, {"language": "AM", "text": "የተተረጎመ", "kind": "machine", "provider": "GOOGLE"})
        self.assertEqual(second.data, first.data)
        translate.assert_called_once()
        q.refresh_from_db()
        self.assertEqual(q.translations["AM"]["text"], "የተተረጎመ")
        # The question as asked is unchanged - translations never replace it.
        self.assertNotEqual(q.question_text, "የተተረጎመ")

    def test_unavailable_translation_is_a_503_not_an_empty_answer(self):
        q = self._questions("EN")[0]
        with patch("api.interviews.question_language.TranslationService.translate", side_effect=AIProcessingError("down")), \
                patch("api.interviews.question_language._openai_translate", side_effect=TranslationUnavailable("down")):
            response = self._get(q, "question-text", question_id=str(q.public_id), language="SW")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["detail"], "translation_unavailable")

    def test_requires_the_session_token_and_a_question_of_this_session(self):
        q = self._questions("EN")[0]
        other = self._questions("AR")[0]
        no_token = self.client.get(f"/api/v1/interviews/{q.session.public_id}/question-text/",
                                   {"question_id": str(q.public_id), "language": "AR"})
        self.assertEqual(no_token.status_code, 403)
        foreign = self._get(q, "question-text", question_id=str(other.public_id), language="AR")
        self.assertEqual(foreign.status_code, 404)

    def test_verbal_statement_is_approved_in_en_ar_and_translated_otherwise(self):
        q = self._questions("EN")[0]
        ar = self._get(q, "prechecks/statement", language="AR")
        self.assertEqual(ar.data["kind"], "approved")
        self.assertIn("أؤكد", ar.data["text"])
        with patch("api.interviews.question_language.TranslationService.translate",
                   return_value={"translated_text": "Ninathibitisha", "provider": "GOOGLE"}):
            sw = self._get(q, "prechecks/statement", language="sw-KE")
        self.assertEqual(sw.data["text"], "Ninathibitisha")
        self.assertEqual(sw.data["kind"], "machine")

    def test_session_payload_exposes_the_candidates_preferred_language(self):
        q = self._questions("AR")[0]
        q.session.candidate.preferred_language = "AM"
        q.session.candidate.save()
        response = self.client.get(f"/api/v1/interviews/{q.session.public_id}/", {"token": q.session.access_token})
        self.assertEqual(response.data["preferred_language"], "AM")

    def test_failing_provider_is_skipped_for_a_while_after_it_fails(self):
        q1, q2 = self._questions("EN")[:2]
        with patch("api.interviews.question_language.TranslationService.translate",
                   side_effect=AIProcessingError("billing disabled")) as google,                 patch("api.interviews.question_language._openai_translate", return_value=("Habari", "OPENAI:test")):
            self._get(q1, "question-text", question_id=str(q1.public_id), language="SW")
            self._get(q2, "question-text", question_id=str(q2.public_id), language="SW")
        google.assert_called_once()

    def test_low_resource_languages_never_use_the_unreliable_fallback(self):
        q = self._questions("EN")[0]
        with patch("api.interviews.question_language.TranslationService.translate", side_effect=AIProcessingError("down")),                 patch("api.interviews.question_language._openai_translate",
                      side_effect=AssertionError("Afar must not use the fallback")):
            response = self._get(q, "question-text", question_id=str(q.public_id), language="AA")
        self.assertEqual(response.status_code, 503)
