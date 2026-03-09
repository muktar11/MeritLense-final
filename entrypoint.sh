#!/bin/sh

echo "Waiting for PostgreSQL to be ready..."

while ! nc -z "$DB_HOST" "$DB_PORT"; do
  sleep 1
done

echo "PostgreSQL is up - continuing..."

python manage.py makemigrations accounts audit candidates contracts core evaluations organizations payments reports subscriptions
python manage.py migrate

python manage.py shell < /app/create_superuser.py

python manage.py runserver 0.0.0.0:8000