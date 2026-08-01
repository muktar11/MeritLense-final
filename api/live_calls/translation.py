import logging

from asgiref.sync import async_to_sync
from django.conf import settings

logger = logging.getLogger(__name__)


class AzureSpeechPipeline:
    """One speaker -> translated speech stream. Input is 16 kHz mono PCM16."""

    VOICES = {
        "en": "en-US-AvaMultilingualNeural",
        "ar": "ar-SA-ZariyahNeural",
        "fr": "fr-FR-DeniseNeural",
        "es": "es-ES-ElviraNeural",
        "de": "de-DE-KatjaNeural",
        "am": "am-ET-MekdesNeural",
    }

    def __init__(self, channel_layer, group_name, sender_role, input_language, target_language):
        if not settings.AZURE_SPEECH_KEY or not settings.AZURE_SPEECH_REGION:
            raise RuntimeError("Azure Speech streaming is not configured")
        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError as exc:
            raise RuntimeError("azure-cognitiveservices-speech is not installed") from exc
        self.sdk = speechsdk
        self.channel_layer = channel_layer
        self.group_name = group_name
        self.sender_role = sender_role
        self.target_locale = target_language
        self.target = target_language.split("-", 1)[0]
        fmt = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
        self.stream = speechsdk.audio.PushAudioInputStream(stream_format=fmt)
        audio = speechsdk.audio.AudioConfig(stream=self.stream)
        config = speechsdk.translation.SpeechTranslationConfig(
            subscription=settings.AZURE_SPEECH_KEY, region=settings.AZURE_SPEECH_REGION
        )
        config.speech_recognition_language = input_language
        config.add_target_language(self.target)
        self.recognizer = speechsdk.translation.TranslationRecognizer(
            translation_config=config, audio_config=audio
        )
        self.recognizer.recognized.connect(self._recognized)
        self.recognizer.canceled.connect(self._canceled)
        self.recognizer.start_continuous_recognition_async().get()

    def write(self, data):
        self.stream.write(data)

    def close(self):
        self.stream.close()
        self.recognizer.stop_continuous_recognition_async().get()

    def _recognized(self, event):
        translated = event.result.translations.get(self.target)
        if not translated:
            return
        speech_config = self.sdk.SpeechConfig(
            subscription=settings.AZURE_SPEECH_KEY, region=settings.AZURE_SPEECH_REGION
        )
        speech_config.speech_synthesis_language = self.target_locale
        voice = self.VOICES.get(self.target)
        if voice:
            speech_config.speech_synthesis_voice_name = voice
        speech_config.set_speech_synthesis_output_format(
            self.sdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
        )
        result = self.sdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None).speak_text_async(translated).get()
        if result.reason != self.sdk.ResultReason.SynthesizingAudioCompleted:
            self._send_error("Speech synthesis failed")
            return
        async_to_sync(self.channel_layer.group_send)(self.group_name, {
            "type": "translation.audio",
            "sender_role": self.sender_role,
            "audio": bytes(result.audio_data),
        })

    def _canceled(self, event):
        logger.warning("Azure streaming translation canceled: %s", event)
        self._send_error("Streaming translation was canceled")

    def _send_error(self, detail):
        async_to_sync(self.channel_layer.group_send)(self.group_name, {
            "type": "translation.error", "sender_role": self.sender_role, "detail": detail
        })
