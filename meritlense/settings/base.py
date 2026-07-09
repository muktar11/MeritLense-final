from datetime import timedelta
from pathlib import Path
import os
import json

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent.parent

_env_file = BASE_DIR / ".env.dev" if (BASE_DIR / ".env.dev").exists() else BASE_DIR / ".env"
load_dotenv(_env_file)


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=None):
    value = os.getenv(name)
    if not value:
        return default or []
    return [item.strip() for item in value.split(",") if item.strip()]


def env_json(name, default=None):
    value = os.getenv(name)
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-dev-only-change-me")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", ["localhost", "127.0.0.1"])

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "channels",
    "drf_spectacular",
    "django_filters",
    "corsheaders",
    "api.accounts",
    "api.dashboard",
    "api.candidates",
    "api.evaluations",
    "api.scores",
    "api.subscriptions",
    "api.payments",
    "api.contracts",
    "api.reports",
    "api.audit",
    "api.core",
    "api.organizations",
    "api.interviews",
    "api.sessions",
    "api.questions",
    "api.notifications",
    "api.translation",
    "api.monitoring",
    "api.storage",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "api.core.middleware.tenant_middleware.TenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

ROOT_URLCONF = "meritlense.urls"
WSGI_APPLICATION = "meritlense.wsgi.application"
ASGI_APPLICATION = "meritlense.asgi.application"
AUTH_USER_MODEL = "accounts.User"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "meritlense_db"),
            "USER": os.getenv("DB_USER", "merit_user"),
            "PASSWORD": os.getenv("DB_PASSWORD", "meritlense_password"),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
USE_REDIS_CHANNEL_LAYER = env_bool("USE_REDIS_CHANNEL_LAYER", False)

if USE_REDIS_CHANNEL_LAYER:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [REDIS_URL],
            },
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "COMPONENT_SPLIT_REQUEST": True,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "USER_AUTHENTICATION_RULE": "rest_framework_simplejwt.authentication.default_user_authentication_rule",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "TOKEN_TYPE_CLAIM": "token_type",
    "JTI_CLAIM": "jti",
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

_email_backend_map = {
    "console": "django.core.mail.backends.console.EmailBackend",
    "smtp": "django.core.mail.backends.smtp.EmailBackend",
}
EMAIL_BACKEND = _email_backend_map.get(
    os.getenv("EMAIL_BACKEND", "smtp"),
    "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_PORT") != "465"
EMAIL_USE_SSL = os.getenv("EMAIL_PORT") == "465"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL")

FRONTEND_URL = os.getenv("FRONTEND_URL", "")

AZURE_QUEUE_CONNECTION_STRING = os.getenv("AZURE_QUEUE_CONNECTION_STRING", "")
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_STORAGE_CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "meritlense-media")
AZURE_DEFAULT_QUEUE_NAME = os.getenv("AZURE_DEFAULT_QUEUE_NAME", "meritlense-jobs")
AZURE_QUEUE_POISON_NAME = os.getenv("AZURE_QUEUE_POISON_NAME", f"{AZURE_DEFAULT_QUEUE_NAME}-poison")
AZURE_QUEUE_VISIBILITY_TIMEOUT_SECONDS = int(os.getenv("AZURE_QUEUE_VISIBILITY_TIMEOUT_SECONDS", "300"))
AZURE_QUEUE_POLL_INTERVAL_SECONDS = float(os.getenv("AZURE_QUEUE_POLL_INTERVAL_SECONDS", "5"))
AZURE_QUEUE_MAX_DEQUEUE_COUNT = int(os.getenv("AZURE_QUEUE_MAX_DEQUEUE_COUNT", "5"))

INTERVIEW_AUDIO_ALLOWED_MIME_TYPES = env_list(
    "INTERVIEW_AUDIO_ALLOWED_MIME_TYPES",
    ["audio/webm", "audio/mp4", "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/ogg"],
)
INTERVIEW_AUDIO_MAX_FILE_SIZE_BYTES = int(os.getenv("INTERVIEW_AUDIO_MAX_FILE_SIZE_BYTES", str(10 * 1024 * 1024)))
INTERVIEW_AUDIO_MAX_DURATION_SECONDS = int(os.getenv("INTERVIEW_AUDIO_MAX_DURATION_SECONDS", "600"))

STT_PROVIDER = os.getenv("STT_PROVIDER", "OPENAI").upper()
STT_API_URL = os.getenv("STT_API_URL", "https://api.openai.com/v1/audio/transcriptions")
STT_API_KEY = os.getenv("STT_API_KEY", "")
STT_MODEL = os.getenv("STT_MODEL", "whisper-1")
STT_TIMEOUT_SECONDS = int(os.getenv("STT_TIMEOUT_SECONDS", "60"))

TTS_PROVIDER = os.getenv("TTS_PROVIDER", "GOOGLE").upper()
TTS_API_URL = os.getenv("TTS_API_URL", "https://texttospeech.googleapis.com/v1/text:synthesize")
GOOGLE_TTS_API_KEY = os.getenv("GOOGLE_TTS_API_KEY", "")
TTS_TIMEOUT_SECONDS = int(os.getenv("TTS_TIMEOUT_SECONDS", "30"))
TTS_AUDIO_ENCODING = os.getenv("TTS_AUDIO_ENCODING", "MP3").upper()
TTS_VOICE_MAP = env_json(
    "TTS_VOICE_MAP",
    {
        "en-US": "en-US-Standard-C",
        "es-ES": "es-ES-Standard-A",
        "fr-FR": "fr-FR-Standard-A",
        "ar-SA": "ar-XA-Standard-A",
        "de-DE": "de-DE-Standard-A",
        "zh-CN": "cmn-CN-Standard-A",
    },
)

TRANSLATION_PROVIDER = os.getenv("TRANSLATION_PROVIDER", "GOOGLE").upper()
TRANSLATION_API_URL = os.getenv("TRANSLATION_API_URL", "https://translation.googleapis.com/language/translate/v2")
GOOGLE_TRANSLATE_API_KEY = os.getenv("GOOGLE_TRANSLATE_API_KEY", "")
GOOGLE_TRANSLATE_PROJECT_ID = os.getenv("GOOGLE_TRANSLATE_PROJECT_ID", "")
TRANSLATION_TIMEOUT_SECONDS = int(os.getenv("TRANSLATION_TIMEOUT_SECONDS", "30"))
DEFAULT_EVALUATION_LANGUAGE = os.getenv("DEFAULT_EVALUATION_LANGUAGE", "EN")

AI_INTERPRETATION_PROVIDER = os.getenv("AI_INTERPRETATION_PROVIDER", "OPENAI").upper()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", os.getenv("STT_API_KEY", ""))
OPENAI_INTERPRETATION_API_URL = os.getenv(
    "OPENAI_INTERPRETATION_API_URL",
    "https://api.openai.com/v1/chat/completions",
)
OPENAI_INTERPRETATION_MODEL = os.getenv("OPENAI_INTERPRETATION_MODEL", "gpt-4o-mini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_INTERPRETATION_API_URL = os.getenv(
    "GEMINI_INTERPRETATION_API_URL",
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
)
GEMINI_INTERPRETATION_MODEL = os.getenv("GEMINI_INTERPRETATION_MODEL", "gemini-1.5-flash")
AI_INTERPRETATION_TIMEOUT_SECONDS = int(os.getenv("AI_INTERPRETATION_TIMEOUT_SECONDS", "45"))
AI_INTERPRETATION_PROMPT_VERSION = os.getenv("AI_INTERPRETATION_PROMPT_VERSION", "week5-v1")
AI_INTERPRETATION_MIN_CONFIDENCE = float(os.getenv("AI_INTERPRETATION_MIN_CONFIDENCE", "0.75"))

ENABLE_TRANSLATION_PIPELINE = env_bool("ENABLE_TRANSLATION_PIPELINE", True)
ENABLE_RESPONSE_INTERPRETATION = env_bool("ENABLE_RESPONSE_INTERPRETATION", True)
ENABLE_RULE_INPUT_PREPARATION = env_bool("ENABLE_RULE_INPUT_PREPARATION", True)
AI_PROCESSING_RETRY_LIMIT = int(os.getenv("AI_PROCESSING_RETRY_LIMIT", "2"))
ENABLE_ASYNC_AI_PROCESSING = env_bool("ENABLE_ASYNC_AI_PROCESSING", True)

STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
CURRENCY = "eur"
TAX_RATE = 0.0

AGREEMENT_OTP_LENGTH = int(os.getenv("AGREEMENT_OTP_LENGTH", "6"))
AGREEMENT_OTP_VALIDITY_MINUTES = int(os.getenv("AGREEMENT_OTP_VALIDITY_MINUTES", "10"))
AGREEMENT_OTP_MAX_ATTEMPTS = int(os.getenv("AGREEMENT_OTP_MAX_ATTEMPTS", "5"))
AGREEMENT_OTP_MAX_RESENDS = int(os.getenv("AGREEMENT_OTP_MAX_RESENDS", "5"))
AGREEMENT_OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("AGREEMENT_OTP_RESEND_COOLDOWN_SECONDS", "60"))

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
