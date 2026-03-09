FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . /app/

RUN echo 'import os\n\
import django\n\
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meritlense.settings")\n\
django.setup()\n\
from django.contrib.auth import get_user_model\n\
from api.core.constants import Roles, DocumentStatus\n\
User = get_user_model()\n\
email = os.getenv("DJANGO_SUPERUSER_EMAIL", "admin@meritlense.com")\n\
password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "Admin@123")\n\
first_name = os.getenv("DJANGO_SUPERUSER_FIRST_NAME", "Super")\n\
last_name = os.getenv("DJANGO_SUPERUSER_LAST_NAME", "Admin")\n\
if not User.objects.filter(email=email).exists():\n\
    print(f"Creating superuser {email}")\n\
    User.objects.create_superuser(\n\
        email=email,\n\
        password=password,\n\
        first_name=first_name,\n\
        last_name=last_name,\n\
        role=Roles.SUPERADMIN,\n\
        is_verified=True,\n\
        documents_verification_status=DocumentStatus.APPROVED\n\
    )\n\
    print("Superuser created successfully")\n\
else:\n\
    print(f"Superuser {email} already exists")' > /app/create_superuser.py

RUN mkdir -p /app/staticfiles /app/media

