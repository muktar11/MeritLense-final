import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from api.storage.services import AzureQueueService
from api.translation.models import IndicatorPhraseTranslation
from api.translation.services import EvaluationInputBuilderService, TranslationService, normalize_language_code


class LanguageNormalizationTests(SimpleTestCase):
    def test_normalizes_full_language_names_from_whisper(self):
        self.assertEqual(normalize_language_code("english"), "en")
        self.assertEqual(normalize_language_code("Spanish"), "es")
        self.assertEqual(normalize_language_code("FRENCH"), "fr")
        self.assertEqual(normalize_language_code("arabic"), "ar")
        self.assertEqual(normalize_language_code("german"), "de")
        self.assertEqual(normalize_language_code("chinese"), "zh")

    def test_normalizes_iso_codes(self):
        self.assertEqual(normalize_language_code("EN"), "en")
        self.assertEqual(normalize_language_code("en-US"), "en")


class IndicatorPhraseReverseLookupTests(TestCase):
    def test_resolves_a_cached_arabic_phrase_back_to_its_english_source(self):
        IndicatorPhraseTranslation.objects.create(
            phrase_en="pull over safely", phrase_ar="توقف بأمان", provider="GOOGLE"
        )

        self.assertEqual(
            TranslationService.resolve_indicator_phrase_to_english("توقف بأمان"),
            "pull over safely",
        )

    def test_returns_the_phrase_unchanged_when_no_cached_mapping_exists(self):
        self.assertEqual(
            TranslationService.resolve_indicator_phrase_to_english("عبارة غير معروفة"),
            "عبارة غير معروفة",
        )


class EvaluationInputBuilderLanguageNormalizationTests(TestCase):
    def _build(self, *, mentioned_steps, missing_steps, input_language):
        response = SimpleNamespace(
            question=SimpleNamespace(question_template=None),
            translated_transcript="",
            translation_status="NOT_REQUIRED",
            session=SimpleNamespace(rubric_version="v1"),
        )
        interpretation = SimpleNamespace(
            normalized_indicators={"mentioned_steps": mentioned_steps, "missing_steps": missing_steps},
            input_language=input_language,
            confidence_score=None,
            prompt_version="v1",
            prompt_hash="hash",
            risk_flags=[],
        )
        return EvaluationInputBuilderService.build(response=response, interpretation=interpretation)

    def test_normalizes_arabic_observed_and_missing_indicators_to_english_for_scoring(self):
        IndicatorPhraseTranslation.objects.create(
            phrase_en="report the fault", phrase_ar="أبلغ عن العطل", provider="GOOGLE"
        )
        IndicatorPhraseTranslation.objects.create(
            phrase_en="inform the employer", phrase_ar="أبلغ صاحب العمل", provider="GOOGLE"
        )

        payload = self._build(
            mentioned_steps=["أبلغ عن العطل"],
            missing_steps=["أبلغ صاحب العمل"],
            input_language="ar",
        )

        self.assertEqual(payload["observed_indicators"], ["report the fault"])
        self.assertEqual(payload["missing_indicators"], ["inform the employer"])

    def test_leaves_english_observed_indicators_untouched(self):
        payload = self._build(
            mentioned_steps=["report the fault"],
            missing_steps=[],
            input_language="en",
        )

        self.assertEqual(payload["observed_indicators"], ["report the fault"])


class AzureQueueWorkerTests(SimpleTestCase):
    def test_decode_job_message_round_trip(self):
        envelope = {
            "job_type": "PROCESS_AI_RESPONSE",
            "payload": {"response_id": "cr_123", "idempotency_key": "request-123"},
        }
        content = base64.b64encode(json.dumps(envelope).encode()).decode()

        self.assertEqual(AzureQueueService.decode_job_message(content), envelope)

    @override_settings(
        AZURE_QUEUE_CONNECTION_STRING="UseDevelopmentStorage=true",
        AZURE_DEFAULT_QUEUE_NAME="meritlense-jobs",
    )
    @patch("api.translation.management.commands.run_ai_queue_worker.AzureQueueService")
    @patch("api.translation.management.commands.run_ai_queue_worker.Command._process_message")
    def test_worker_deletes_successful_message(self, process_message, service_class):
        message = SimpleNamespace(id="message-1", pop_receipt="receipt-1", dequeue_count=1, content="content")
        queue = Mock()
        queue.receive_messages.return_value.by_page.return_value = iter([[message]])
        service_class.return_value.is_configured = True
        service_class.return_value.queue_name = "meritlense-jobs"
        service_class.return_value.get_client.return_value = queue

        call_command("run_ai_queue_worker", max_messages=1)

        process_message.assert_called_once_with(service_class.return_value, message)
        queue.delete_message.assert_called_once_with("message-1", "receipt-1")

    @override_settings(
        AZURE_QUEUE_CONNECTION_STRING="UseDevelopmentStorage=true",
        AZURE_DEFAULT_QUEUE_NAME="meritlense-jobs",
        AZURE_QUEUE_POISON_NAME="meritlense-jobs-poison",
        AZURE_QUEUE_MAX_DEQUEUE_COUNT=5,
    )
    @patch("api.translation.management.commands.run_ai_queue_worker.AzureQueueService")
    @patch("api.translation.management.commands.run_ai_queue_worker.Command._process_message")
    def test_worker_moves_exhausted_message_to_poison_queue(self, process_message, service_class):
        message = SimpleNamespace(id="message-2", pop_receipt="receipt-2", dequeue_count=5, content="content")
        source_queue = Mock()
        poison_queue = Mock()
        source_queue.receive_messages.return_value.by_page.return_value = iter([[message]])
        service = service_class.return_value
        service.is_configured = True
        service.queue_name = "meritlense-jobs"
        service.get_client.side_effect = lambda queue_name=None: poison_queue if queue_name else source_queue
        process_message.side_effect = RuntimeError("provider unavailable")

        call_command("run_ai_queue_worker", max_messages=1)

        poison_queue.create_queue.assert_called_once()
        poison_queue.send_message.assert_called_once_with("content")
        source_queue.delete_message.assert_called_once_with("message-2", "receipt-2")
