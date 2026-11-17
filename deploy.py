#!/usr/bin/env python3
"""Docker helper for Hookd.

    python3 deploy.py build          Build the app image
    python3 deploy.py services       Start the Postgres and Redis containers (--recreate to apply config changes)
    python3 deploy.py run            Open a shell in the development app container
    python3 deploy.py deploy         Same as run, with the production mount and DJANGO_DEBUG=false
    python3 deploy.py install-cron   Install host cron entries that run manage.py jobs in the app container

Three containers share the hookd_net network: Postgres and Redis run detached with a restart policy and
keep their data in named volumes, so they stay up while the app container is replaced. Inside the app
container you start everything yourself: `run` (or `runbg` for a detached screen) starts the Celery
worker in the background, then the Django dev server. Uses only the standard library.
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
ENV_FILE = ROOT / ".env"
HOST_LOG_DIR = ROOT / "logs"
CONTAINER_LOG_DIR = "/home/logs"

APP_IMAGE = "localhost/hookd:latest"
APP_CONTAINER_NAME = "hookd_app_dev"

NETWORK = "hookd_net"
TIMEZONE = "Asia/Kolkata"

POSTGRES_IMAGE = "postgres:18-alpine"
POSTGRES_CONTAINER_NAME = "hookd_postgres"
POSTGRES_VOLUME = "hookd_pgdata"
# Sized for a 4 CPU, 8 GB server shared with the app, Celery and Redis. Data lives on SSD.
POSTGRES_SETTINGS = {
    "shared_buffers": "1GB",
    "effective_cache_size": "3GB",
    "work_mem": "16MB",
    "maintenance_work_mem": "256MB",
    "max_connections": "50",
    "random_page_cost": "1.1",
}

REDIS_IMAGE = "redis:8-alpine"
REDIS_CONTAINER_NAME = "hookd_redis"
REDIS_VOLUME = "hookd_redis"

# Repository mount point inside the app container, per mode.
MODE_HOMES = {
    "development": "/home/development",
    "production": "/home/production",
}

# Host cron jobs that call manage.py inside the app container. Each line carries a
# marker comment so install-cron can replace it in place instead of duplicating it.
CRON_JOBS = [
    # Daily: delete Celery task results older than CELERY_RESULT_EXPIRES (7 days).
    ("cleanup_task_results", "30 3 * * *", ["cleanup_task_results"]),
    # Every 10 minutes: send queued comment replies whose Celery task was lost (never sends twice).
    ("send_due_replies", "*/10 * * * *", ["send_due_replies"]),
]


def execute(command, check=True, quiet=False, hide=()):
    """Runs a command after printing it, with every value in `hide` (passwords) masked."""
    shown = " ".join(command)
    for secret in hide:
        shown = shown.replace(secret, "***")
    print(f"Executing: {shown}")
    return subprocess.run(command, check=check, text=True, capture_output=quiet)


def read_env():
    """Parses .env the way `docker --env-file` does: KEY=VALUE lines, no quotes, # comments."""
    if not ENV_FILE.exists():
        sys.exit("Missing .env. Copy .env.example to .env and fill it in first.")
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value
    return values


def require_env(env, *names):
    missing = [name for name in names if not env.get(name)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} in .env first.")


def is_running(container):
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container], capture_output=True, text=True
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def ensure_network():
    if subprocess.run(["docker", "network", "inspect", NETWORK], capture_output=True).returncode != 0:
        execute(["docker", "network", "create", NETWORK])


def service_command(name, image, volume, mount, memory, env_args, args):
    # No published ports: only containers on hookd_net can reach the service. Docker's published ports
    # bypass ufw, so publishing them would expose the service to the internet.
    return [
        "docker", "run", "-d",
        "--name", name,
        "--network", NETWORK,
        "--restart", "unless-stopped",
        "--memory", memory,
        "-v", f"{volume}:{mount}",
        "--env", f"TZ={TIMEZONE}",
        *env_args,
        image,
        *args,
    ]


def postgres_command(env):
    settings = [arg for key, value in POSTGRES_SETTINGS.items() for arg in ("-c", f"{key}={value}")]
    return service_command(
        POSTGRES_CONTAINER_NAME, POSTGRES_IMAGE, POSTGRES_VOLUME, "/var/lib/postgresql", "2g",
        [
            "--shm-size", "256m",
            "--env", f"POSTGRES_DB={env['POSTGRES_DB']}",
            "--env", f"POSTGRES_USER={env['POSTGRES_USER']}",
            "--env", f"POSTGRES_PASSWORD={env['POSTGRES_PASSWORD']}",
        ],
        ["postgres", *settings],
    )


def redis_command(env):
    # A broker must never drop queued tasks, so Redis refuses writes when full instead of evicting keys.
    return service_command(
        REDIS_CONTAINER_NAME, REDIS_IMAGE, REDIS_VOLUME, "/data", "512m", [],
        [
            "redis-server",
            "--appendonly", "yes",
            "--maxmemory", "256mb",
            "--maxmemory-policy", "noeviction",
            "--requirepass", env["REDIS_PASSWORD"],
        ],
    )


def wait_for_postgres(env):
    check = ["docker", "exec", POSTGRES_CONTAINER_NAME, "pg_isready", "-q", "-U", env["POSTGRES_USER"]]
    for _ in range(60):
        if subprocess.run(check, capture_output=True).returncode == 0:
            return
        time.sleep(1)
    sys.exit(f"Postgres did not become ready. See: docker logs {POSTGRES_CONTAINER_NAME}")


def start_services(recreate=False):
    env = read_env()
    require_env(env, "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "REDIS_PASSWORD")
    ensure_network()
    passwords = (env["POSTGRES_PASSWORD"], env["REDIS_PASSWORD"])
    for name, command in (
        (POSTGRES_CONTAINER_NAME, postgres_command(env)),
        (REDIS_CONTAINER_NAME, redis_command(env)),
    ):
        if is_running(name) and not recreate:
            print(f"{name} is running")
            continue
        # Data is in a named volume, so replacing the container keeps it.
        execute(["docker", "rm", "-f", name], check=False, quiet=True)
        execute(command, hide=passwords)
    wait_for_postgres(env)


def start_app(mode):
    start_services()
    # Created on the host first so Docker does not create it as root owned.
    HOST_LOG_DIR.mkdir(parents=True, exist_ok=True)

    home = MODE_HOMES[mode]
    execute(["docker", "rm", "-f", APP_CONTAINER_NAME], check=False, quiet=True)
    command = [
        "docker", "run", "-it", "--rm",
        "--name", APP_CONTAINER_NAME,
        "--network", NETWORK,
        "--workdir", f"{home}/backend",
        "-p", "8000:8000",
        "-v", f"{ROOT}:{home}",
        "-v", f"{HOST_LOG_DIR}:{CONTAINER_LOG_DIR}",
        "--env-file", str(ENV_FILE),
        "--env", f"APP_HOME={home}",
        # Shell and screen use India time too (Django sets its own from TIME_ZONE).
        "--env", f"TZ={TIMEZONE}",
    ]
    if mode == "production":
        command += ["--env", "DJANGO_DEBUG=false"]
    # The exit code of the interactive shell is not an error for this script.
    execute([*command, APP_IMAGE], check=False)


def build(_args):
    print("Building the app image...")
    execute(["docker", "build", str(ROOT), "-t", APP_IMAGE, "-f", str(ROOT / "Dockerfile")])


def services(args):
    start_services(recreate=args.recreate)


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
    services_parser = commands.add_parser("services", help="Start the Postgres and Redis containers")
    services_parser.add_argument("--recreate", action="store_true", help="Replace them to apply config changes")
    services_parser.set_defaults(handler=services)
    commands.add_parser("run", help="Open a shell in the development app container").set_defaults(handler=run)
    commands.add_parser("deploy", help="Open a shell in the production app container").set_defaults(handler=deploy)
    cron = commands.add_parser("install-cron", help="Install host cron entries for scheduled manage.py jobs")
    cron.add_argument("--remove", action="store_true", help="Remove the hookd cron entries instead")
    cron.set_defaults(handler=install_cron)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
