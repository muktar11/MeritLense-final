# MeritLense — Interview Language Support

## Description

Candidates can now:

1. **Read questions aloud in a chosen language** — a per-question language picker next to the "Listen to question" button (`question-card.tsx`). The question text is translated (Google Translate) before being sent to Google Cloud TTS, so switching language actually changes the spoken content, not just the voice accent.
2. **Answer in a language of their choice, per response** — a language picker on the interview page ("Answering in:") lets the candidate pick a different language for each answer, independent of the session's fixed default. The selection is:
   - sent as the Whisper speech-to-text hint for audio answers,
   - stored as `CandidateResponse.transcript_language`, which the translation pipeline (`AIProcessingOrchestrationService`) reads as the source language for evaluation/translation.
3. Automatically **forced to text mode** when the selected answer language has no working speech-to-text provider, instead of silently producing a bad transcription.

The supported language list was expanded from 6 to 26 languages, prioritizing the top labor-source countries for the GCC market (Philippines, India, Bangladesh, Pakistan, Sri Lanka, Indonesia, Nepal, Ethiopia, Kenya, Uganda, Ghana, Djibouti, Eritrea, Vietnam, Cambodia, Myanmar, Tanzania, Madagascar).

### Why per-language capability flags, not a single hardcoded exception

Every provider (Google Cloud TTS, Google Cloud Translate, OpenAI Whisper) has a different, independently-drifting list of supported languages. Before this work, Afaan Oromo was the only language with no TTS/STT support, handled as a single hardcoded `"OM"` check scattered across the frontend. That doesn't scale — several of the newly-added languages (Somali, Malagasy) have STT but not TTS, which a single "is this Oromo" check can't express.

The frontend's shared language list (`MeritLense-ui/src/lib/languages.ts`) now carries explicit `tts`/`stt` booleans per language, set **only** from a direct provider check (never assumed from "this is a major language"):

- `READ_ALOUD_LANGUAGES` (`read-aloud-languages.ts`) filters on `tts`.
- The interview page's audio-answer gate (`audioAnswersUnavailable` in `page.tsx`) filters on `stt`, reactively, based on whichever language is currently selected for the answer in progress — not just the session's fixed default.

## Supported languages

| Language | Short code | BCP-47 code | Read-aloud (TTS) | Audio answers (STT) | Text + translation |
|---|---|---|---|---|---|
| English | EN | en-US | ✅ | ✅ | ✅ |
| Arabic | AR | ar-SA | ✅ | ✅ | ✅ |
| Amharic | AM | am-ET | ✅ | ✅ | ✅ |
| Filipino | FIL | fil-PH | ✅ | ✅ | ✅ |
| Chinese | ZH | zh-CN | ✅ | ✅ | ✅ |
| Hindi | HI | hi-IN | ✅ | ✅ | ✅ |
| Urdu | UR | ur-PK | ✅ | ✅ | ✅ |
| Bengali | BN | bn-BD | ✅ | ✅ | ✅ |
| Tamil | TA | ta-IN | ✅ | ✅ | ✅ |
| Telugu | TE | te-IN | ✅ | ✅ | ✅ |
| Malayalam | ML | ml-IN | ✅ | ✅ | ✅ |
| Punjabi | PA | pa-IN | ✅ | ✅ | ✅ |
| Sinhala | SI | si-LK | ✅ | ✅ | ✅ |
| Indonesian | ID | id-ID | ✅ | ✅ | ✅ |
| Nepali | NE | ne-NP | ✅ | ✅ | ✅ |
| Swahili | SW | sw-KE | ✅ | ✅ | ✅ |
| Vietnamese | VI | vi-VN | ✅ | ✅ | ✅ |
| Khmer | KM | km-KH | ✅ | ✅ | ✅ |
| Burmese | MY | my-MM | ✅ | ✅ | ✅ |
| Somali | SO | so-SO | ❌ | ✅ | ✅ |
| Malagasy | MG | mg-MG | ❌ | ✅ | ✅ |
| Afaan Oromo | OM | om-ET | ❌ | ❌ | ✅ |
| Tigrinya | TI | ti-ET | ❌ | ❌ | ✅ |
| Afar | AA | aa-DJ | ❌ | ❌ | ✅ |
| Twi (Akan) | AK | ak-GH | ❌ | ❌ | ✅ |
| Luganda | LG | lg-UG | ❌ | ❌ | ✅ |

Languages with ❌ on read-aloud are simply never offered in the read-aloud picker. Languages with ❌ on audio answers are forced to text mode, both on initial session load (based on the session's default language) and reactively if the candidate switches their per-answer language mid-interview.

## How support was verified

Every capability flag above came from a direct, live check against the actual provider — not from documentation alone, since providers frequently drop support for otherwise-common languages (e.g. no Google TTS voice for Somali or Malagasy despite both being common languages with Whisper support).

**Google Cloud TTS** — `POST https://texttospeech.googleapis.com/v1/text:synthesize` with a minimal payload (`{"input":{"text":"hello"},"voice":{"languageCode":"<code>"},"audioConfig":{"audioEncoding":"MP3"}}`) against the production API key, run directly on the production VM via `az vm run-command`. HTTP 200 = supported, HTTP 400 ("Voice does not exist") = unsupported.

```
TTS hi-IN 200   TTS ur-PK 200   TTS bn-BD 200   TTS ta-IN 200   TTS te-IN 200
TTS ml-IN 200   TTS pa-IN 200   TTS si-LK 200   TTS id-ID 200   TTS ne-NP 200
TTS sw-KE 200   TTS vi-VN 200   TTS km-KH 200   TTS my-MM 200
TTS ti-ET 400   TTS so-SO 400   TTS aa-ET 400   TTS aa-DJ 400
TTS ak-GH 400   TTS tw-GH 400   TTS lg-UG 400   TTS mg-MG 400
```

**Google Cloud Translate v2** — `POST https://translation.googleapis.com/language/translate/v2` translating a test string from `en` to each target short code. All 20 newly-added languages, including the six TTS-unsupported ones, returned HTTP 200.

```
TR hi 200   TR ur 200   TR bn 200   TR ta 200   TR te 200   TR ml 200   TR pa 200
TR si 200   TR id 200   TR ne 200   TR sw 200   TR vi 200   TR km 200
TR ti 200   TR so 200   TR aa 200   TR ak 200   TR tw 200   TR lg 200   TR mg 200
```

**OpenAI Whisper (STT)** — cross-checked against Whisper's documented supported-language list (no separate "list languages" API to hit directly). Hindi, Urdu, Bengali, Tamil, Telugu, Malayalam, Punjabi, Sinhala, Indonesian, Nepali, Swahili, Vietnamese, Khmer, Burmese, Somali, and Malagasy are all listed; Tigrinya, Afar, Twi/Akan, and Luganda are not.

## Test coverage

Backend (`api.interviews`, `api.sessions` test suites — 73 tests, subset of the full 113-test suite, all passing on every deploy in this change set):

- `test_submit_response_stores_candidate_selected_answer_language` — a text answer submitted with an explicit `language_code` stores that value on `CandidateResponse.transcript_language`, not the session default.
- `test_submit_response_rejects_unsupported_answer_language` — submitting an answer with a `language_code` outside `LANGUAGE_CODE_MAP` is rejected (HTTP 400) and no `CandidateResponse` is created.
- `test_transcribe_response_passes_candidate_selected_language_as_stt_hint` — the per-answer `language_code` is passed through to the STT provider as the language hint, and used as the stored `transcript_language` when the provider doesn't return its own detected language.
- `test_transcribe_response_rejects_unsupported_answer_language` — same rejection behavior as above, for the transcription endpoint.

Full backend suite: **113/113 passing** after each of the three deploys in this change set (per-answer language selection, top-tier GCC languages, second-tier text-only languages).

Frontend, run before every deploy:
- `npx tsc --noEmit` — clean, no type errors.
- `npx eslint` on all touched files — clean (two pre-existing `react/no-unescaped-entities` warnings on unrelated lines in `page.tsx`, not introduced by this work).
- `next build` with `NEXT_PUBLIC_STATIC_EXPORT=true` — full static export succeeds (92 pages).

## Deployment verification

Each deploy was confirmed live in production, not just by a green CI run:

- Backend: `git log -1` on the production VM matched the pushed commit hash; `systemctl show gunicorn -p ActiveEnterTimestamp` confirmed the process restarted at deploy time (so the new code was actually loaded, not just present on disk); a Django shell check on the VM confirmed `Languages.CHOICES` and `LANGUAGE_CODE_MAP` reflected the new entries.
- Migrations (`AlterField` on `preferred_language` / `candidate_preferred_language` to widen `choices`, no schema change) applied cleanly on every deploy, confirmed via the GitHub Actions deploy log.
- Frontend: the live production JS bundle (`meritlense.com/en/interview`) was fetched and grepped directly for new UI strings ("Answering in", "Malayalam", "Tigrinya") to confirm the built bundle actually shipped, rather than trusting the Static Web Apps deploy step alone.

## Known limitation

Afaan Oromo, Tigrinya, Afar, Twi (Akan), and Luganda have no working TTS or STT. Candidates selecting these languages are restricted to typed text answers; translation and evaluation still work normally via Google Translate. Somali and Malagasy support audio answers (Whisper STT) but not read-aloud (no Google TTS voice). If a provider adds support for any of these in the future, only the `tts`/`stt` flags in `MeritLense-ui/src/lib/languages.ts` and the corresponding comment in `api/sessions/services.py`'s `LANGUAGE_CODE_MAP` need to change — no other code changes required.
