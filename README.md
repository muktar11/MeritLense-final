# MeritLense Backend

Backend foundation for the MeritLense AI Interview and Evaluation platform.

## Stack

- Django
- Django REST Framework
- SimpleJWT
- PostgreSQL
- drf-spectacular Swagger/OpenAPI
- Azure Queue and Azure Storage placeholders for deployment jobs and media

Redis, Celery, and Django Channels are intentionally not part of the Week 1 setup.

## Local Setup With Remote Production Database

Use this when you want to run the backend code locally but connect to the real remote database.

Important: be careful with real production data. Do not run destructive commands, test deletes, seed scripts, or experimental migrations against production unless the team approves it.

### 1. Create Virtual Environment

Windows PowerShell:

```powershell
python -m venv .venv

or

python3 -m venv .venv
```

If `python` is not found, try:

```powershell
py -3.12 -m venv .venv

```

### 2. Activate Virtual Environment

```powershell
.\.venv\Scripts\Activate.ps1


or 

source .venv/bin/activate
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Create Local `.env`

Copy the template:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env. 

or 

cp .env.example .env
```

Update `.env` with the real remote database values:

```env
DJANGO_SETTINGS_MODULE=meritlense.settings.development
DEBUG=True
DJANGO_SECRET_KEY=your-local-dev-secret

ALLOWED_HOSTS=localhost,127.0.0.1
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

DB_NAME=production_db_name
DB_USER=production_db_user
DB_PASSWORD=production_db_password
DB_HOST=production_db_host
DB_PORT=5432
```

Keep production-only secrets such as payment keys and Azure keys out of local `.env` unless the feature you are testing needs them.

### 5. Check Django Configuration

```powershell
python manage.py check
```

### 6. Apply Migrations

Only run this if you intentionally want to apply migrations to the remote database:

```powershell
python manage.py migrate
```

Before changing models, generate and review migrations locally:

```powershell
python manage.py makemigrations
```

### 7. Start Local Server

```powershell
python manage.py runserver

The backend runs at:

```text
http://127.0.0.1:8000
```

Health check:

```text
http://127.0.0.1:8000/api/v1/health/
```

Swagger:

```text
http://127.0.0.1:8000/api/v1/docs/
```

## API

Health check:

```text
GET /api/v1/health/
```

Swagger:

```text
/api/v1/docs/
```

Schema:

```text
/api/v1/schema/
```

All application API routes should use the `/api/v1/` prefix.

```
## Optional Docker Setup

Use Docker only if you want local PostgreSQL and a containerized backend.

```bash
docker compose up --build
```

The Docker backend runs at:

```text
http://localhost:8020
```

## Default Docker Admin

```text
Email: admin@meritlense.com
Password: Admin@123
```

## Migrations

Generate migrations with local venv:

```powershell
python manage.py makemigrations
```

Apply migrations with local venv:

```powershell
python manage.py migrate
```

Optional Docker commands:

```bash
docker compose run --rm web python manage.py makemigrations
docker compose run --rm web python manage.py migrate
```

Migration packages are tracked in git. Commit generated migration files when models change.

## Azure Configuration

Set these variables for Azure-backed jobs and media:

```text
AZURE_QUEUE_CONNECTION_STRING=
AZURE_DEFAULT_QUEUE_NAME=meritlense-jobs
AZURE_STORAGE_CONNECTION_STRING=
AZURE_STORAGE_CONTAINER_NAME=meritlense-media
```

Use `api.storage.services.enqueue_background_job()` when a feature needs to submit a background job.

## Week 4 Voice Pipeline

The backend now supports the Week 4 interview voice loop:

- `GET /api/v1/interviews/{id}/current-question/`
- `POST /api/v1/interviews/{id}/question-audio/`
- `POST /api/v1/interviews/{id}/upload-response-audio/`
- `POST /api/v1/interviews/{id}/transcribe-response/`

Voice pipeline behavior:

- Question audio is generated through the configured TTS provider and cached per session question.
- Candidate voice answers are stored as first-class response records with file metadata.
- Transcription is handled through a provider abstraction and persists transcript plus provider trace metadata.
- Critical voice events are written into the audit log for traceability.

### Voice Environment Variables

```text
INTERVIEW_AUDIO_ALLOWED_MIME_TYPES=audio/webm,audio/mp4,audio/mpeg,audio/mp3,audio/wav,audio/x-wav,audio/ogg
INTERVIEW_AUDIO_MAX_FILE_SIZE_BYTES=10485760
INTERVIEW_AUDIO_MAX_DURATION_SECONDS=600

STT_PROVIDER=OPENAI
STT_API_URL=https://api.openai.com/v1/audio/transcriptions
STT_API_KEY=
STT_MODEL=whisper-1
STT_TIMEOUT_SECONDS=60

TTS_PROVIDER=GOOGLE
TTS_API_URL=https://texttospeech.googleapis.com/v1/text:synthesize
GOOGLE_TTS_API_KEY=
TTS_TIMEOUT_SECONDS=30
TTS_AUDIO_ENCODING=MP3
TTS_VOICE_MAP={"en-US":"en-US-Standard-C","es-ES":"es-ES-Standard-A"}
```

### Example Flow

1. Start or resume a valid interview session.
2. Call `GET /current-question/` to activate the next question in deterministic order.
3. Call `POST /question-audio/` to get a cached or newly generated audio artifact for that question.
4. Call `POST /upload-response-audio/` with multipart form data containing `question_id`, `audio_file`, and `duration_seconds`.
5. Call `POST /transcribe-response/` with the returned `response_id` to persist the transcript and STT metadata.

### Sample Requests

Current question:

```bash
curl -X GET \
  http://127.0.0.1:8000/api/v1/interviews/{session_id}/current-question/ \
  -H "Authorization: Bearer <staff-jwt>"
```

Question audio:

```bash
curl -X POST \
  http://127.0.0.1:8000/api/v1/interviews/{session_id}/question-audio/ \
  -H "Authorization: Bearer <staff-jwt>" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Upload response audio:

```bash
curl -X POST \
  http://127.0.0.1:8000/api/v1/interviews/{session_id}/upload-response-audio/ \
  -H "X-Session-Token: <session-token>" \
  -F "question_id=<question-public-id>" \
  -F "duration_seconds=18" \
  -F "audio_file=@answer.webm;type=audio/webm"
```

Transcribe response:

```bash
curl -X POST \
  http://127.0.0.1:8000/api/v1/interviews/{session_id}/transcribe-response/ \
  -H "Authorization: Bearer <staff-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"response_id":"<candidate-response-public-id>"}'
```

### Sample Responses

Question audio response:

```json
{
  "id": "artifact-public-id",
  "question_id": "question-public-id",
  "provider": "GOOGLE",
  "voice_name": "en-US-Standard-C",
  "language_code": "en-US",
  "audio_url": "/media/interviews/sessions/12/questions/34/tts/file.mp3",
  "mime_type": "audio/mpeg",
  "file_size_bytes": 24876,
  "duration_estimate_seconds": 6,
  "metadata": {
    "audio_encoding": "MP3",
    "character_count": 87
  },
  "generated_at": "2026-06-24T08:10:00Z"
}
```

Transcription response:

```json
{
  "id": "response-public-id",
  "question":  "question-public-id",
  "response_type": "VOICE",
  "audio_url": "/media/interviews/sessions/12/responses/file.webm",
  "audio_mime_type": "audio/webm",
  "audio_file_size_bytes": 98123,
  "transcript": "I would keep the child safe first.",
  "original_transcript": "I would keep the child safe first.",
  "transcript_language": "en",
  "stt_provider": "OPENAI",
  "stt_model": "whisper-1",
  "stt_request_id": "req_123",
  "stt_confidence": null,
  "stt_status": "COMPLETED",
  "stt_error_code": "",
  "stt_error_message": "",
  "duration_seconds": 18
}
```

### Audit Events

The voice pipeline writes these key actions:

- `AUDIO_UPLOAD_STARTED`
- `AUDIO_UPLOAD_COMPLETED`
- `QUESTION_AUDIO_GENERATED`
- `TRANSCRIPTION_REQUESTED`
- `TRANSCRIPTION_COMPLETED`
- `TRANSCRIPTION_FAILED`
- `RESPONSE_ATTACHED`
- `SESSION_MOVED_TO_NEXT_QUESTION`

Example audit payload fields:

```json
{
  "session_id": "session-public-id",
  "question_id": "question-public-id",
  "candidate_response_id": "response-public-id",
  "access_context": "session_token",
  "provider": "OPENAI",
  "request_id": "req_123",
  "processing_status": "COMPLETED"
}
```

### Week 4 Guardrail

This Week 4 implementation does not introduce AI scoring or hiring decisions. Voice services are limited to audio storage, transcription, synthesis, and traceability so later evaluation modules can consume clean artifacts without violating the project rule: `AI Assists - Rules Decide - Humans Hire`.
