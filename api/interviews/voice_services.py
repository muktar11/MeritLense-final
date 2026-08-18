import base64
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings


class VoiceProviderError(Exception):
    def __init__(self, message, *, code="provider_error", metadata=None):
        super().__init__(message)
        self.code = code
        self.metadata = metadata or {}


class VoiceProviderConfigurationError(VoiceProviderError):
    pass


class SpeechToTextService:
    def __init__(self):
        self.provider = settings.STT_PROVIDER
        self.api_url = settings.STT_API_URL
        self.api_key = settings.STT_API_KEY
        self.model = settings.STT_MODEL
        self.timeout_seconds = settings.STT_TIMEOUT_SECONDS

    @property
    def is_configured(self):
        return bool(self.api_url and self.api_key and self.model)

    def transcribe(self, *, file_obj, filename, mime_type, language_code=""):
        if not self.is_configured:
            raise VoiceProviderConfigurationError(
                "Speech-to-text provider is not configured",
                code="stt_not_configured",
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "model": self.model,
            "response_format": "verbose_json",
        }
        if language_code:
            data["language"] = language_code.split("-")[0].lower()

        response = self._post(data, file_obj, filename, mime_type, headers)

        # The provider's `language` hint only accepts a fixed enum of codes -
        # some codes this app otherwise treats as STT-capable (e.g. Amharic)
        # aren't in it, even though the underlying model can often still
        # transcribe them reasonably via auto-detection. Retry once without
        # the hint rather than failing the whole request outright, but only
        # for that specific rejection reason - any other 400 should still
        # surface as a real error.
        if data.get("language") and response.status_code == 400 and self._rejected_unsupported_language(response):
            data.pop("language")
            file_obj.seek(0)
            response = self._post(data, file_obj, filename, mime_type, headers)

        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise VoiceProviderError(
                "Speech-to-text provider request failed",
                code="stt_request_failed",
                metadata={"status_code": response.status_code},
            ) from exc

        payload = response.json()
        transcript = (payload.get("text") or payload.get("transcript") or "").strip()
        confidence = self._extract_confidence(payload)

        return {
            "provider": self.provider,
            "provider_model": payload.get("model") or self.model,
            "request_id": response.headers.get("x-request-id", ""),
            "detected_language": payload.get("language") or language_code,
            "confidence": confidence,
            "processing_status": "COMPLETED",
            "transcript": transcript,
            "metadata": {
                "duration": payload.get("duration"),
                "segments": payload.get("segments", []),
                "words": payload.get("words", []),
            },
        }

    def _post(self, data, file_obj, filename, mime_type, headers):
        try:
            return requests.post(
                self.api_url,
                headers=headers,
                data=data,
                files={"file": (filename, file_obj, mime_type)},
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise VoiceProviderError(
                "Speech-to-text provider timed out",
                code="stt_timeout",
            ) from exc
        except requests.RequestException as exc:
            raise VoiceProviderError(
                "Speech-to-text provider request failed",
                code="stt_request_failed",
            ) from exc

    @staticmethod
    def _rejected_unsupported_language(response):
        try:
            body = response.json()
        except ValueError:
            return False
        return (body.get("error") or {}).get("code") == "unsupported_language"

    def _extract_confidence(self, payload):
        confidence = payload.get("confidence")
        if confidence is None and payload.get("segments"):
            scores = [segment.get("avg_logprob") for segment in payload["segments"] if segment.get("avg_logprob") is not None]
            if scores:
                confidence = max(min(sum(scores) / len(scores) + 1, 1), 0)

        if confidence in (None, ""):
            return None

        try:
            return Decimal(str(confidence)).quantize(Decimal("0.0001"))
        except (InvalidOperation, TypeError, ValueError):
            return None


class TextToSpeechService:
    MIME_TYPES = {
        "MP3": "audio/mpeg",
        "LINEAR16": "audio/wav",
        "OGG_OPUS": "audio/ogg",
    }

    def __init__(self):
        self.provider = settings.TTS_PROVIDER
        self.api_url = settings.TTS_API_URL
        self.api_key = settings.GOOGLE_TTS_API_KEY
        self.timeout_seconds = settings.TTS_TIMEOUT_SECONDS
        self.audio_encoding = settings.TTS_AUDIO_ENCODING
        self.voice_map = settings.TTS_VOICE_MAP or {}

    @property
    def is_configured(self):
        return bool(self.api_url and self.api_key)

    def synthesize(self, *, text, language_code):
        if not self.is_configured:
            raise VoiceProviderConfigurationError(
                "Text-to-speech provider is not configured",
                code="tts_not_configured",
            )

        # Only send a voice name when we actually have one mapped for this
        # exact language - forcing the English voice name onto an unmapped
        # language would send Google a mismatched language/voice pair
        # (e.g. Arabic text with an English-named voice), which risks the
        # wrong accent or pronunciation. Omitting it lets Google pick an
        # appropriate default voice for the requested language on its own.
        voice_name = self.voice_map.get(language_code, "")
        voice_payload = {"languageCode": language_code}
        if voice_name:
            voice_payload["name"] = voice_name
        payload = {
            "input": {"text": text},
            "voice": voice_payload,
            "audioConfig": {
                "audioEncoding": self.audio_encoding,
            },
        }

        try:
            response = requests.post(
                f"{self.api_url}?key={self.api_key}",
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise VoiceProviderError(
                "Text-to-speech provider timed out",
                code="tts_timeout",
            ) from exc
        except requests.RequestException as exc:
            status_code = getattr(exc.response, "status_code", None)
            raise VoiceProviderError(
                "Text-to-speech provider request failed",
                code="tts_request_failed",
                metadata={"status_code": status_code},
            ) from exc

        body = response.json()
        audio_content = body.get("audioContent")
        if not audio_content:
            raise VoiceProviderError(
                "Text-to-speech provider returned no audio content",
                code="tts_empty_response",
            )

        audio_bytes = base64.b64decode(audio_content)
        return {
            "provider": self.provider,
            "voice_name": voice_name,
            "language_code": language_code,
            "mime_type": self.MIME_TYPES.get(self.audio_encoding, "application/octet-stream"),
            "audio_bytes": audio_bytes,
            "duration_estimate_seconds": self._estimate_duration_seconds(text),
            "metadata": {
                "audio_encoding": self.audio_encoding,
                "character_count": len(text),
            },
        }

    def _estimate_duration_seconds(self, text):
        words = len([word for word in text.split() if word.strip()])
        if not words:
            return 1
        return max(1, round(words / 2.5))
