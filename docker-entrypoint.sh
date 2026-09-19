#!/bin/sh
set -e

# Only the web process (daphne) owns migrations/static collection, so the
# celery worker/beat containers - which use this same image - don't race it
# on every restart.
if [ "$1" = "daphne" ]; then
  echo "Applying database migrations..."
  python manage.py migrate --noinput

  echo "Collecting static files..."
  python manage.py collectstatic --noinput

  # Opt-in and safe to leave on for a couple of deploys - seed_demo uses
  # get_or_create for almost everything, so it won't duplicate data. But it
  # WILL recreate any demo product you've deleted by slug, since as far as
  # it knows that product just doesn't exist yet - so once you've cleaned
  # the catalog up, unset RUN_SEED_DEMO (or set it to false) so your
  # deletions actually stick on the next deploy.
  if [ "${RUN_SEED_DEMO:-false}" = "true" ]; then
    echo "RUN_SEED_DEMO=true - seeding demo data..."
    python manage.py seed_demo
  fi
fi

exec "$@"
