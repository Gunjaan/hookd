#!/usr/bin/env bash
# Starts the app's processes inside the container. The Dockerfile aliases call this script; it lives in the
# mounted repository, so edits apply without rebuilding the image.
#
#   hookd.sh run       Stop everything, then start the Celery worker in the background and the Django dev
#                      server in the foreground. Running it again always gives one fresh copy of each.
#   hookd.sh serve     Production: collect static files, start the Celery worker, then run gunicorn in the
#                      foreground. `deploy.py deploy` runs this as the container command.
#   hookd.sh celery    Restart only the Celery worker (screen session "celery")
#   hookd.sh stop      Stop the web server (dev server or gunicorn) and the worker
#
# Postgres and Redis run in their own containers (deploy.py services), so they are not started here.
# With DJANGO_DEBUG=true the worker restarts by itself when a Python file under backend/ changes.
set -euo pipefail

BACKEND="$APP_HOME/backend"
LOG_DIR=/home/logs

SERVER_PATTERN="manage.py runserver|gunicorn config.wsgi"
CELERY_PATTERN="celery -A config worker"

# Sends TERM to every process matching the pattern, waits up to 10 s, then KILLs what is left.
stop_matching() {
  local pattern="$1"
  pkill -TERM -f "$pattern" 2>/dev/null || return 0
  for _ in $(seq 50); do
    pgrep -f "$pattern" >/dev/null || return 0
    sleep 0.2
  done
  pkill -KILL -f "$pattern" 2>/dev/null || true
}

stop_server() {
  stop_matching "$SERVER_PATTERN"
}

stop_celery() {
  # Covers the screen session, watchfiles and every worker process, however many were started.
  stop_matching "$CELERY_PATTERN"
  screen -wipe >/dev/null 2>&1 || true
}

start_celery() {
  stop_celery
  local worker="$CELERY_PATTERN --loglevel=INFO --concurrency=2"
  if [ "${DJANGO_DEBUG:-false}" = "true" ]; then
    worker="watchfiles --filter python '$worker' $BACKEND"
  fi
  screen -dmS celery bash -c "cd $BACKEND && $worker 2>&1 | tee -a $LOG_DIR/celery.log"
  echo "Celery worker started (screen -r celery to watch, Ctrl-a d to detach)"
}

case "${1:-}" in
  run)
    stop_server
    start_celery
    cd "$BACKEND"
    exec python manage.py runserver 0.0.0.0:8000
    ;;
  serve)
    stop_server
    cd "$BACKEND"
    python manage.py collectstatic --noinput
    start_celery
    # Three workers, not 2 x CPU + 1: Celery and Postgres share the 4 cores. Access log goes to docker logs.
    exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --threads 2 --timeout 60 \
      --access-logfile -
    ;;
  celery)
    start_celery
    ;;
  stop)
    stop_server
    stop_celery
    echo "Stopped the web server and the Celery worker"
    ;;
  *)
    echo "Usage: hookd.sh run|serve|celery|stop" >&2
    exit 2
    ;;
esac
