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
```

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
