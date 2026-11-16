#!/usr/bin/env bash
# Starts the app's processes inside the container. The Dockerfile aliases call this script; it lives in the
# mounted repository, so edits apply without rebuilding the image.
#
#   hookd.sh run       Stop everything, then start Redis and the Celery worker in the background and the
#                      Django dev server in the foreground. Running it again always gives one fresh copy of each.
#   hookd.sh celery    Restart only the Celery worker (screen session "celery")
#   hookd.sh stop      Stop the dev server, the worker and Redis
#
# With DJANGO_DEBUG=true the worker restarts by itself when a Python file under backend/ changes.
set -euo pipefail

BACKEND="$APP_HOME/backend"
REDIS_DIR="$APP_HOME/data/redis"
LOG_DIR=/home/logs

SERVER_PATTERN="manage.py runserver"
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

stop_redis() {
  if redis-cli ping >/dev/null 2>&1; then
    redis-cli shutdown >/dev/null 2>&1 || true
  fi
  stop_matching "redis-server"
}

start_redis() {
  if redis-cli ping >/dev/null 2>&1; then
    return
  fi
  mkdir -p "$REDIS_DIR"
  redis-server --daemonize yes --bind 127.0.0.1 --port 6379 \
    --dir "$REDIS_DIR" --appendonly yes --logfile "$LOG_DIR/redis.log"
  until redis-cli ping >/dev/null 2>&1; do
    sleep 0.2
  done
  echo "Redis running on 127.0.0.1:6379"
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
    stop_celery
    stop_redis
    start_redis
    start_celery
    cd "$BACKEND"
    exec python manage.py runserver 0.0.0.0:8000
    ;;
  celery)
    start_redis
    start_celery
    ;;
  stop)
    stop_server
    stop_celery
    stop_redis
    echo "Stopped the dev server, the Celery worker and Redis"
    ;;
  *)
    echo "Usage: hookd.sh run|celery|stop" >&2
    exit 2
    ;;
esac
