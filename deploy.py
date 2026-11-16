#!/usr/bin/env python3
"""Docker helper for Hookd.

    python3 deploy.py build          Build the app image
    python3 deploy.py run            Open a shell in the development app container
    python3 deploy.py deploy         Same as run, with the production mount and DJANGO_DEBUG=false
    python3 deploy.py install-cron   Install host cron entries that run manage.py jobs in the app container

Inside the container you start everything yourself: `run` (or `runbg` for a detached screen) starts
Redis and the Celery worker in the background, then the Django dev server; `restart-celery` restarts
the worker. The database is SQLite (backend/db.sqlite3) and Redis runs inside the same container,
so there are no other containers. Uses only the standard library.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
ENV_FILE = ROOT / ".env"
HOST_LOG_DIR = ROOT / "logs"
CONTAINER_LOG_DIR = "/home/logs"

APP_IMAGE = "localhost/hookd:latest"
APP_CONTAINER_NAME = "hookd_app_dev"

# Repository mount point inside the app container, per mode.
MODE_HOMES = {
    "development": "/home/development",
    "production": "/home/production",
}

# Host cron jobs that call manage.py inside the app container. Each line carries a
# marker comment so install-cron can replace it in place instead of duplicating it.
CRON_JOBS = [
    # Daily: refresh platform access tokens that expire within 10 days.
    ("refresh_social_tokens", "0 3 * * *", ["refresh_social_tokens"]),
    # Daily: delete Celery task results older than CELERY_RESULT_EXPIRES (7 days).
    ("cleanup_task_results", "30 3 * * *", ["cleanup_task_results"]),
    # Every 10 minutes: send queued comment replies whose Celery task was lost (never sends twice).
    ("send_due_replies", "*/10 * * * *", ["send_due_replies"]),
]


def execute(command, check=True, quiet=False):
    print(f"Executing: {' '.join(command)}")
    return subprocess.run(command, check=check, text=True, capture_output=quiet)


def start_app(mode):
    if not ENV_FILE.exists():
        sys.exit("Missing .env. Copy .env.example to .env and fill it in first.")
    # Created on the host first so Docker does not create it as root owned.
    HOST_LOG_DIR.mkdir(parents=True, exist_ok=True)

    home = MODE_HOMES[mode]
    execute(["docker", "rm", "-f", APP_CONTAINER_NAME], check=False, quiet=True)
    command = [
        "docker", "run", "-it", "--rm",
        "--name", APP_CONTAINER_NAME,
        "--workdir", f"{home}/backend",
        "-p", "8000:8000",
        "-v", f"{ROOT}:{home}",
        "-v", f"{HOST_LOG_DIR}:{CONTAINER_LOG_DIR}",
        "--env-file", str(ENV_FILE),
        "--env", f"APP_HOME={home}",
        # Shell, Redis and screen use India time too (Django sets its own from TIME_ZONE).
        "--env", "TZ=Asia/Kolkata",
    ]
    if mode == "production":
        command += ["--env", "DJANGO_DEBUG=false"]
    # The exit code of the interactive shell is not an error for this script.
    execute([*command, APP_IMAGE], check=False)


def build(_args):
    print("Building the app image...")
    execute(["docker", "build", str(ROOT), "-t", APP_IMAGE, "-f", str(ROOT / "Dockerfile")])


def run(_args):
    start_app("development")


def deploy(_args):
    start_app("production")


CRON_MARKER_PREFIX = "# hookd:"


def cron_marker(name):
    return f"{CRON_MARKER_PREFIX}{name}"


def read_crontab():
    # `crontab -l` exits non-zero when the user has no crontab yet.
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return result.stdout.splitlines() if result.returncode == 0 else []


def cron_line(name, schedule, manage_args, docker_bin):
    command = " ".join([docker_bin, "exec", APP_CONTAINER_NAME, "python", "manage.py", *manage_args])
    log_file = HOST_LOG_DIR / f"cron_{name}.log"
    return f"{schedule} {command} >> {log_file} 2>&1 {cron_marker(name)}"


def install_cron(args):
    docker_bin = shutil.which("docker")
    if not docker_bin and not args.remove:
        sys.exit("docker not found on PATH; cron needs its absolute path.")

    # Drop every hookd entry, including jobs no longer in CRON_JOBS, then add the current ones.
    lines = [line for line in read_crontab() if CRON_MARKER_PREFIX not in line]
    if not args.remove:
        HOST_LOG_DIR.mkdir(parents=True, exist_ok=True)
        lines += [cron_line(name, schedule, manage_args, docker_bin) for name, schedule, manage_args in CRON_JOBS]

    content = "\n".join(lines) + "\n" if lines else ""
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)
    for name, schedule, _ in CRON_JOBS:
        print(f"{'Removed' if args.remove else 'Installed'} {name} ({schedule})")


def main():
    parser = argparse.ArgumentParser(description="Docker helper for Hookd.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="Build the app image").set_defaults(handler=build)
    commands.add_parser("run", help="Open a shell in the development app container").set_defaults(handler=run)
    commands.add_parser("deploy", help="Open a shell in the production app container").set_defaults(handler=deploy)
    cron = commands.add_parser("install-cron", help="Install host cron entries for scheduled manage.py jobs")
    cron.add_argument("--remove", action="store_true", help="Remove the hookd cron entries instead")
    cron.set_defaults(handler=install_cron)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
