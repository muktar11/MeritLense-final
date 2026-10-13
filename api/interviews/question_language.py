"""What the candidate sees and hears in each language during an interview.

English and Arabic are the only approved source languages: an Arabic
session asks the approved Arabic questions, every other session the
approved English ones. Any other language is a translation of that source
question - shown next to it, never instead of it, and never fed into
scoring (answers keep their own transcript/translation pipeline).

Display translations try the configured translation provider first, then
the OpenAI model already used for interpretation, so a single provider
outage doesn't leave the candidate without a translation. Each translation
is stored on the SessionQuestion, so a reviewer can see exactly what the
candidate was shown.
"""
import json
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from api.translation.services import AIProcessingError, TranslationService

logger = logging.getLogger(__name__)

SOURCE_LANGUAGES = ("EN", "AR")

# Mirrors candidate-lang.ts (verbalConfirmation.phrase) in MeritLense-ui -
# the approved statement; other languages are translations of the English.
VERBAL_CONFIRMATION_STATEMENT = {
    "EN": "I confirm that I am completing this assessment myself and that these are my own answers.",
    "AR": "أؤكد أنني أجري هذا التقييم بنفسي، وأن الإجابات التي أقدمها هي إجاباتي الشخصية.",
}

KIND_ORIGINAL = "original"   # the question exactly as asked in this session
KIND_APPROVED = "approved"   # the approved EN/AR version of the same question
KIND_MACHINE = "machine"     # automatic translation


class TranslationUnavailable(Exception):
    """No provider could translate the text; the UI says so and keeps
    showing the original."""


def language_key(value):
    """'am-ET' / 'am' / 'AM' -> 'AM'; 'fil-PH' -> 'FIL'."""
    return (value or "").split("-")[0].strip().upper()


def _openai_translate(text, source_key, target_key):
    api_key = getattr(settings, "OPENAI_API_KEY", "")
    url = getattr(settings, "OPENAI_INTERPRETATION_API_URL", "")
    # A stronger model than interpretation's default: short texts, stored
    # once per question, and gpt-4o-mini's Amharic/Tigrinya were unreadable.
    model = getattr(settings, "OPENAI_DISPLAY_TRANSLATION_MODEL", "") or getattr(settings, "OPENAI_INTERPRETATION_MODEL", "")
    if not (api_key and url and model):
        raise TranslationUnavailable("OpenAI is not configured")
    prompt = (
        "Translate the text from language code "
        f"{source_key.lower()} to language code {target_key.lower()}. "
        "Keep the meaning exact and the tone neutral; do not add, explain or answer anything. "
        'Respond with JSON: {"translation": "<translated text>"}.\n\n'
        f"Text:\n{text}"
    )
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": "You are a precise translator. Respond with valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=20,
        )
        response.raise_for_status()
        content = ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
        translated = (json.loads(content).get("translation") or "").strip()
    except (requests.RequestException, ValueError) as exc:
        raise TranslationUnavailable(f"OpenAI translation failed: {exc}") from exc
    if not translated:
        raise TranslationUnavailable("OpenAI returned an empty translation")
    return translated, f"OPENAI:{model}"


PROVIDER_DOWN_KEY = "display-translation:provider-down"
PROVIDER_DOWN_SECONDS = 600


def display_translate(text, source_key, target_key):
    """(translated_text, provider) - configured provider first, then
    OpenAI. Raises TranslationUnavailable when neither works. After the
    provider fails it is skipped for 10 minutes, so candidates don't wait
    on a provider that is down (e.g. billing disabled) for every question."""
    if not cache.get(PROVIDER_DOWN_KEY):
        try:
            result = TranslationService.translate(
                text=text, source_language=source_key.lower(), target_language=target_key.lower(),
            )
            if result.get("translated_text"):
                return result["translated_text"], result.get("provider") or settings.TRANSLATION_PROVIDER
        except AIProcessingError as exc:
            logger.info("Display translation provider failed (%s); trying OpenAI", getattr(exc, "code", exc))
            cache.set(PROVIDER_DOWN_KEY, True, PROVIDER_DOWN_SECONDS)
        except Exception:  # noqa: BLE001 - provider misconfiguration must not break the interview
            logger.exception("Display translation provider raised unexpectedly; trying OpenAI")
            cache.set(PROVIDER_DOWN_KEY, True, PROVIDER_DOWN_SECONDS)
    return _openai_translate(text, source_key, target_key)


def question_source_language(question):
    template = question.question_template
    key = language_key(template.language if template else "") or language_key(question.session.candidate_language)
    return key if key in SOURCE_LANGUAGES else "EN"


def question_text_in(question, target):
    """The current question in `target` language, as a dict:
    {language, text, kind, provider}. Approved EN/AR wording is used
    whenever it exists; anything else is translated (and stored)."""
    target_key = language_key(target)
    source_key = question_source_language(question)
    if not target_key or target_key == source_key:
        return {"language": source_key, "text": question.question_text, "kind": KIND_ORIGINAL, "provider": ""}

    stored = (question.translations or {}).get(target_key)
    if stored and stored.get("text"):
        return {"language": target_key, **stored}

    entry = None
    template = question.question_template
    if target_key in SOURCE_LANGUAGES and template and template.question_code:
        from api.questions.models import QuestionTemplate

        sibling = (
            QuestionTemplate.objects.filter(
                question_code=template.question_code,
                question_set_version=template.question_set_version,
                language__iexact=target_key,
            )
            .exclude(question_text="")
            .order_by("-is_active", "-id")
            .first()
        )
        if sibling is not None:
            entry = {"text": sibling.question_text, "kind": KIND_APPROVED, "provider": ""}

    if entry is None:
        text, provider = display_translate(question.question_text, source_key, target_key)
        entry = {"text": text, "kind": KIND_MACHINE, "provider": provider}

    translations = dict(question.translations or {})
    translations[target_key] = entry
    question.translations = translations
    question.save(update_fields=["translations", "updated_at"])
    return {"language": target_key, **entry}


def statement_in(target):
    """The verbal-confirmation statement in `target` language."""
    target_key = language_key(target)
    if target_key in VERBAL_CONFIRMATION_STATEMENT:
        return {"language": target_key, "text": VERBAL_CONFIRMATION_STATEMENT[target_key], "kind": KIND_APPROVED, "provider": ""}
    cache_key = f"verbal-statement:{target_key}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    text, provider = display_translate(VERBAL_CONFIRMATION_STATEMENT["EN"], "EN", target_key)
    entry = {"language": target_key, "text": text, "kind": KIND_MACHINE, "provider": provider}
    cache.set(cache_key, entry, 60 * 60 * 24)
    return entry
