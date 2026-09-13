#!/bin/sh
set -e

# Only the web process (gunicorn) owns migrations/static collection, so the
# celery worker/beat containers - which use this same image - don't race it
# on every restart.
if [ "$1" = "gunicorn" ]; then
  echo "Applying database migrations..."
  python manage.py migrate --noinput

  echo "Collecting static files..."
  python manage.py collectstatic --noinput
fi

exec "$@"
