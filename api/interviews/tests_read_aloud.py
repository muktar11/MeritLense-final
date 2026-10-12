"""Read-aloud (text-to-speech) language selection for interview questions.

Arabic sessions ask the approved Arabic templates. The voice must read the
approved text in the chosen language - not a machine translation of it, and
never Arabic text through an English voice (or the reverse)."""
from unittest.mock import patch

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
        return patch("api.sessions.services.TranslationService.translate",
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
        with patch("api.sessions.services.TranslationService.translate",
                   return_value={"translated_text": "texto"}) as translate:
            self.assertEqual(InterviewVoicePipelineService._read_aloud_text(q, "es-ES"), "texto")
        translate.assert_called_once_with(text=q.question_text, source_language="ar", target_language="es")

    def test_translation_failure_falls_back_to_the_on_screen_text(self):
        q = self._questions("EN")[0]
        with patch("api.sessions.services.TranslationService.translate", side_effect=AIProcessingError("down")):
            self.assertEqual(InterviewVoicePipelineService._read_aloud_text(q, "am-ET"), q.question_text)
