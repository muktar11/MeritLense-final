#!/bin/sh
set -e

echo "Starting SSH service..."
service ssh start

echo "Running database migrations..."
python manage.py migrate --noinput

echo "Starting application..."
exec "$@"
