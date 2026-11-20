# DEV: Infrastructure

## Docker (`deploy.py` + `Dockerfile`)

Three containers on the `hookd_net` Docker network, sized for one 4 CPU, 8 GB VPS:

| Container | Image | Lifetime | Data | Reached at |
|---|---|---|---|---|
| `hookd_postgres` | `postgres:18-alpine` | detached, `--restart unless-stopped`, `--memory 2g` | volume `hookd_pgdata` at `/var/lib/postgresql` | `hookd_postgres:5432` |
| `hookd_redis` | `redis:8-alpine` | detached, `--restart unless-stopped`, `--memory 512m` | volume `hookd_redis` at `/data` (append only file) | `hookd_redis:6379` |
| `hookd_app_dev` | `localhost/hookd:latest` | `run`: interactive, `--rm`. `deploy`: detached, `--restart unless-stopped`. Replaced on every `run` or `deploy` | repo mount | `run`: host port `8000`. `deploy`: `127.0.0.1:8000` only |

Postgres and Redis publish **no ports**: only containers on `hookd_net` reach them. Do not add `-p` for them, Docker's
published ports bypass `ufw` and would expose them to the internet. Replacing either container keeps its data (named
volume). From the host: `docker exec -it hookd_postgres psql -U hookd hookd`, `docker exec -it hookd_redis redis-cli`
(then `AUTH <REDIS_PASSWORD>`), `docker logs hookd_postgres`.

The app container holds the Python 3.13 venv at `/home/venv` (no Node: the UI is Django templates) plus `psql`
for `manage.py dbshell`. The repository is **mounted, not copied**, so code edits on the host show up immediately.
In development the container opens a bash shell and you start the app yourself; in production it runs
`scripts/hookd.sh serve` (gunicorn and Celery) as its command.

`deploy.py` uses only the Python standard library. It reads `.env` itself (same rules as `docker --env-file`) for the
`POSTGRES_*` and `REDIS_PASSWORD` values it passes to the service containers.

| Command | Does |
|---|---|
| `python3 deploy.py build` | Builds `localhost/hookd:latest` from `Dockerfile` (build context only sends `backend/requirements.txt`, see `.dockerignore`) |
| `python3 deploy.py services [--recreate]` | Creates `hookd_net` if missing, starts `hookd_postgres` and `hookd_redis` if they are not running, waits for `pg_isready`. `--recreate` replaces both to apply changed settings or passwords |
| `python3 deploy.py run` | Runs `services`, removes any old `hookd_app_dev`, then `docker run -it --rm` with the repo at `/home/development`, workdir `/home/development/backend` |
| `python3 deploy.py deploy` | Runs `services`, removes any old `hookd_app_dev`, then starts it detached with `--restart unless-stopped`, the repo at `/home/production`, `DJANGO_DEBUG=false`, `TERM=xterm` (for screen) and port `127.0.0.1:8000`, running `hookd.sh serve`. See Production |
| `python3 deploy.py install-cron [--remove]` | Writes host crontab lines that `docker exec hookd_app_dev python manage.py ...` (see Cron) |

`run` and `deploy` pass:

| Flag | Value |
|---|---|
| network | `hookd_net` |
| ports | `run`: `8000`. `deploy`: `127.0.0.1:8000` (only Caddy on the host reaches it; a public published port would bypass `ufw`) |
| mounts | repo at `$APP_HOME`; `./logs` at `/home/logs` |
| env | `--env-file .env`, `APP_HOME=/home/development` or `/home/production`, `TZ=Asia/Kolkata` |

Rebuild the image after changing `requirements.txt` or the `Dockerfile`.

### Service settings (in `deploy.py`)

| Service | Settings | Why |
|---|---|---|
| Postgres (`POSTGRES_SETTINGS`) | `shared_buffers=1GB`, `effective_cache_size=3GB`, `work_mem=16MB`, `maintenance_work_mem=256MB`, `max_connections=50`, `random_page_cost=1.1`, `--shm-size 256m` | Leaves room for the app on 8 GB; SSD storage |
| Redis | `--appendonly yes`, `--maxmemory 256mb`, `--maxmemory-policy noeviction`, `--requirepass $REDIS_PASSWORD` | A broker must never evict queued tasks, so Redis refuses writes when full instead |

The database and user are created from `POSTGRES_DB`, `POSTGRES_USER` and `POSTGRES_PASSWORD` only when the volume is
empty (first start). The user is a superuser, so `manage.py test` can create its test database. Changing them later
means changing them inside Postgres (`ALTER USER ...`) too.

Memory budget on the VPS: Postgres about 2 GB, Redis 300 MB, gunicorn about 1.5 GB, Celery about 1 GB, the rest for
Caddy, the OS and page cache. Add a 2 GB swap file on the server.

### Inside the container

Shell aliases from the `Dockerfile` (`/root/.bashrc`) call `scripts/hookd.sh`, which is in the mounted repo, so
changing it needs no rebuild:

| Alias | Does |
|---|---|
| `run` | Stops any running dev server and Celery worker, starts the worker in the `celery` screen session, then runs `python manage.py runserver 0.0.0.0:8000` in the foreground. Running it again (or `runbg`) always leaves exactly one of each |
| `runbg` | Starts `run` in a detached `screen` session named `backend` (replacing the previous one) |
| `restart-celery` | Restarts only the Celery worker |
| `stopall` | Stops the web server (dev server or gunicorn) and the worker (Postgres and Redis keep running in their containers) |

`scripts/hookd.sh serve` (no alias, the production container command) stops any web server, runs
`collectstatic --noinput`, starts the worker, then `exec`s gunicorn
(`config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --threads 2 --timeout 60 --access-logfile -`), so gunicorn is
the container's main process and its access log is `docker logs hookd_app_dev`.

Stopping works by command line pattern (`pkill`/`pgrep` from `procps`): TERM, wait up to 10 s, then KILL. It
matches `manage.py runserver`, `gunicorn config.wsgi` and `celery -A config worker` (screen session, watchfiles and worker processes), so it
also cleans up copies started by hand.

- **Celery worker:** `celery -A config worker --loglevel=INFO --concurrency=2`. With `DJANGO_DEBUG=true` it runs
  under `watchfiles`, which restarts it when a Python file under `backend/` changes (like runserver). Output goes to
  the screen session and `logs/celery.log`.
- Stopping `run` with `Ctrl-c` stops only the dev server; the worker keeps running until the next `run` or `stopall`.
- `python3 deploy.py run` force removes any existing `hookd_app_dev` container before starting, so there is never
  more than one app container.

Screen basics: `screen -ls`, `screen -r backend` or `screen -r celery`, detach with `Ctrl-a d`.

First time setup inside a fresh container:

```bash
python manage.py migrate
run
```

Open http://localhost:8000. Templates and static files are served by Django's dev server, so edits show up on reload.
A second shell into the running container: `docker exec -it hookd_app_dev bash`.

## Database and cache

- **Postgres** (`hookd_postgres`) through `psycopg` 3. `DATABASES` reads the `POSTGRES_*` env vars, with
  `CONN_MAX_AGE = 60` and `CONN_HEALTH_CHECKS = True` (connections are reused and checked, so a Postgres restart
  does not break running processes).
- **Redis** (`hookd_redis`): database 0 is the Celery broker, database 1 the cache. Settings build `REDIS_URL` from
  `REDIS_HOST` and `REDIS_PASSWORD` (URL quoted).
- **Cache:** Django's `RedisCache` on database 1, key prefix `hookd`, shared by every server and worker process.
  Holds the studio's recent posts and account insights (`DEV-content.md`). `manage.py test` uses `LocMemCache`
  instead, so tests need no Redis and never clear the real cache.
- **Uploads:** `MEDIA_ROOT = backend/media/` (gitignored). Publish uploads (`publish/`, deleted once published or
  failed, served to platforms by the `publish-asset` view) and post studio photos (`creatives/sources/` and
  `creatives/slides/`, deleted with their draft, served to the owner by `creative-file`). No web server config needed.

## Environment variables

All read in `backend/config/settings.py` via helpers in `backend/config/env.py` (`env_str`, `env_bool`, `env_int`, `env_list`).
`.env` is passed with `docker --env-file`, so values must not be quoted.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | yes | | Django signing key (also signs OAuth state) |
| `DJANGO_DEBUG` | | `false` | Debug mode; when false, secure cookie and proxy SSL settings turn on |
| `DJANGO_ALLOWED_HOSTS` | | `localhost,127.0.0.1` | Comma list |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | | | Comma list of HTTPS origins the site is opened from (tunnel, production). Every form post needs it |
| `LOG_LEVEL` | | `INFO` | Root log level |
| `POSTGRES_DB` | yes | `hookd` | Database name. Read by `deploy.py` (creates it on first start) and the app |
| `POSTGRES_USER` | yes | `hookd` | Database user, same |
| `POSTGRES_PASSWORD` | yes | | Database password, same |
| `POSTGRES_HOST` | | `hookd_postgres` | Database host on `hookd_net` |
| `POSTGRES_PORT` | | `5432` | Database port |
| `REDIS_PASSWORD` | yes | | Redis `requirepass`. Read by `deploy.py` and the app (broker and cache URLs). After changing it run `deploy.py services --recreate` |
| `REDIS_HOST` | | `hookd_redis` | Redis host on `hookd_net` |
| `APP_URL` | | `http://localhost:8000` | Public base URL, used for default OAuth redirect URIs and publish media URLs. Must be HTTPS (tunnel or domain) for publishing |
| `FIELD_ENCRYPTION_KEYS` | yes for tokens | | Comma list of Fernet keys, newest first. `manage.py generate_encryption_key` |
| `PROVIDER_HTTP_TIMEOUT_SECONDS` | | `15` | Timeout for platform API calls |
| `EMAIL_HOST` | for real mail | | SMTP server, `smtpout.secureserver.net` (GoDaddy Workspace Email). Empty means the console email backend (mail is printed to the Celery worker log) |
| `EMAIL_PORT` | | `465` | `465` turns on implicit SSL (`EMAIL_USE_SSL`), any other port (`587`) uses STARTTLS (`EMAIL_USE_TLS`) |
| `EMAIL_HOST_USER` | | `support@hookd.world` | Mailbox to log in with. Also the sender: `DEFAULT_FROM_EMAIL = SERVER_EMAIL = Hookd <EMAIL_HOST_USER>`, so mail passes SPF and DMARC |
| `EMAIL_HOST_PASSWORD` | for real mail | | That mailbox's password |
| `INSTAGRAM_*` | for Instagram | | See `DEV-instagram.md` (includes `INSTAGRAM_WEBHOOK_FIELDS`, default `comments`) |
| `ANTHROPIC_API_KEY` | for AI | | Claude API key for all AI text: post studio plans and captions, the reply agent loop, brain facts (`apps/ai/claude.py`). The reply agent uses Claude only when it is set |
| `CLAUDE_COPY_MODEL` | | `claude-haiku-5-5` | Model that reads product photos, plans shots and writes captions |
| `CLAUDE_REPLY_MODEL` | | `claude-haiku-5-5` | Model that writes comment replies and learns brain facts from captions and inbox replies (`DEV-agent.md`) |
| `GEMINI_API_KEY` | for AI photos | | Google AI Studio key for rendering post studio photos (`apps/ai/gemini.py`) |
| `GEMINI_IMAGE_MODEL` | | `gemini-nano-banana-2.1` | Image model that renders post photos |

Not an env var: `AI_MODEL_PRICES` in `config/settings.py` holds USD per million input and output tokens per model, used
to record the cost of every post studio AI call (`DEV-creatives.md`). Add an entry when changing `CLAUDE_COPY_MODEL` or
`GEMINI_IMAGE_MODEL`; a model without one is recorded at cost 0 with a warning in the log.

### Email (GoDaddy Workspace Email)

`hookd.world` mail is hosted on GoDaddy Workspace Email (secureserver.net): MX `smtp.secureserver.net` and
`mailstore1.secureserver.net`, SPF `v=spf1 include:secureserver.net -all`, DMARC with `p=quarantine`. Because SPF only
allows secureserver.net, outgoing mail must go through GoDaddy's SMTP, `smtpout.secureserver.net` port 465 (SSL) or
587 (STARTTLS), logging in with the full mailbox address and its password. Another provider (for example Titan) would
fail authentication and SPF. Test from the container after changing `.env` and restarting it:
`python manage.py sendtestemail support@hookd.world`. A 535 error means a wrong address or password.
`EMAIL_TIMEOUT = 60` because some SMTP servers delay their greeting by about 20 s; a timeout at 60 s means outbound SMTP
is blocked, so try the other port.

The reply agent's model, sending limits and loop limits are plain settings (`HOOKD_AGENT_MODEL`, `AGENT_REPLY_LIMITS`,
`AGENT_LOOP_LIMITS`), not env vars; see `DEV-agent.md`. The post studio engines and daily cap are too (`HOOKD_COPY_ENGINE`, `HOOKD_IMAGE_ENGINE`,
`CREATIVE_DAILY_LIMIT`); see `DEV-creatives.md`.

## Time zone

India time everywhere it is shown: `TIME_ZONE = 'Asia/Kolkata'` and `CELERY_TIMEZONE = TIME_ZONE` in settings,
`TZ=Asia/Kolkata` on all three containers (shell, Postgres and Redis logs). `USE_TZ = True`, so the database stores UTC and templates,
admin and log timestamps show IST. Host cron schedules use the host's clock, so set the server to IST as well
(`timedatectl set-timezone Asia/Kolkata`) or the cron schedules are read in its zone.

## Cron (scheduled jobs)

No celery beat. Jobs are Django management commands run by the **host** crontab through `docker exec`,
listed in `CRON_JOBS` in `deploy.py`. Each crontab line ends with a `# hookd:<name>` marker so
`install-cron` replaces entries in place. Output goes to `logs/cron_<name>.log`. The app container must be running.

| Schedule | Command | Purpose |
|---|---|---|
| `30 3 * * *` | `cleanup_task_results` | Delete stored Celery task results older than 7 days |
| `*/10 * * * *` | `send_due_replies` | Send queued comment replies more than 5 minutes past their slot (lost Celery tasks). A reply is never sent twice |
| `*/10 * * * *` | `wake_agent_runs` | Expire seller questions open for 24 hours (their runs wrap up), send queued DMs more than 5 minutes past their slot and wake agent runs whose follow-up is more than 5 minutes overdue (lost Celery tasks). A DM is never sent twice |

Adding a job: write the management command, add a tuple to `CRON_JOBS`, document it here, run
`python3 deploy.py install-cron`. `install-cron` first removes every line carrying a `# hookd:` marker, so jobs
dropped from `CRON_JOBS` disappear too.

## Celery

`backend/config/celery.py` creates the app (`hookd`), settings use the `CELERY_` prefix, tasks are autodiscovered
from `apps/*/tasks.py`. Broker: `CELERY_BROKER_URL`, built from `REDIS_URL` (Redis database 0). `CELERY_TASK_ACKS_LATE` with prefetch 1, so a task is re-delivered if the worker dies
mid task. `manage.py test` sets `TESTING`, which turns on `CELERY_TASK_ALWAYS_EAGER` so tests need no broker.

Conventions: a task only calls one service function (`apps/content/tasks.py` is the example). Queue it with
`transaction.on_commit(lambda: task.apply_async(...))` when it reads rows written in the same request; tests use
`captureOnCommitCallbacks` and patch `apply_async`.

| Task | Queued by | Does |
|---|---|---|
| `apps.accounts.tasks.send_access_request(details)` | `accounts.services.request_access` | Emails an access request to `SUPPORT_EMAIL`. Retries up to 5 times with backoff on SMTP or network errors. Returns `sent` |
| `apps.agent.tasks.handle_comment(comment_id)` | `comments.services.record_incoming_comments` (on commit) | Starts an agent run for a new webhook comment. Returns the outcome (`started`, `skipped`, `ignored`, `exists`, `missing`) |
| `apps.agent.tasks.handle_buyer_message(conversation_id, message_id)` | `agent.receivers.answer_buyer_message` on `messaging.signals.customer_message_received` (on commit) | Starts or continues the buyer's agent run. Returns `queued`, `handed_off`, `ignored` or `missing` |
| `apps.agent.tasks.advance_run(run_id)` | `agent.services` when a run gets an event (on commit); follow-ups with `eta` = when they are due | Runs the agent loop. Returns the run status or `busy` (another worker holds it). A long follow-up `eta` may be redelivered; the run claim makes that harmless, and `wake_agent_runs` catches lost ones |
| `apps.agent.tasks.send_reply(reply_id)`, `send_dm(dm_id)` | `agent.services.schedule_reply` / `schedule_dm` (on commit, `eta` = the send slot) | Sends one public reply or DM within the limits; returns `sent`, `failed`, `capped`, `paused`, `rate_limited`, `not_due` or `not_queued`. Redis may redeliver long `eta` tasks after its visibility timeout; the claim and `not_due` checks make that harmless |
| `apps.agent.tasks.email_ticket(ticket_id)` | `agent.services.create_ticket` (on commit) | Emails a support ticket to `SUPPORT_EMAIL` once, retrying up to 5 times with backoff on SMTP or network errors. Returns `sent`, `already_sent` or `missing` |
| `apps.creatives.tasks.generate_creative(creative_id)` | `creatives.services.start_creative` (on commit) | One copy engine call plans the post, then queues a render per slide. Returns the draft status |
| `apps.creatives.tasks.render_slide(slide_id)` | `generate_creative` and `regenerate_slide` (on commit) | One image engine call for one slide, then settles the draft. Returns the slide status |
| `apps.content.tasks.poll_publish_job(job_id)` | `content.services.schedule_publish_poll` | Checks a processing upload, re-queues itself every 20 s until published, failed or timed out. Returns the job status (`processing`, `published`, `failed`, `missing`) |

### Task results

`django-celery-results` stores every task run in the database (`CELERY_RESULT_BACKEND = 'django-db'`, table
`django_celery_results_taskresult`). See them in the Django admin at **Celery Results > Task results**
(`/admin/django_celery_results/taskresult/`): filter by status or task name, open a row for the arguments, return value,
worker and full traceback.

| Status | Meaning |
|---|---|
| `STARTED` | Running now (`CELERY_TASK_TRACK_STARTED`) |
| `SUCCESS` | Finished; `result` is the JSON return value (tasks return something meaningful, for example the publish job status) |
| `FAILURE` | Raised; `result` has the exception, `traceback` the stack |
| `RETRY` | Raised `self.retry` and will run again |

`CELERY_RESULT_EXTENDED` adds task name, args, kwargs and worker. A row appears when the task starts, so tasks waiting
in the queue (including ones queued with a `countdown`) are not listed; see those with
`celery -A config inspect scheduled` (countdown) and `celery -A config inspect reserved`. Rows older than
`CELERY_RESULT_EXPIRES` (7 days) are deleted by the `cleanup_task_results` cron job.

Check the worker from a container shell: `celery -A config inspect ping`.

## Testing and quality (inside the container)

```bash
python manage.py test
```

## Production (GoDaddy VPS, Caddy)

One Ubuntu VPS. The repository is cloned to **`/home/gunjan/hookd`** by the `gunjan` user (in the `docker` group). The
`Caddyfile` points there; Caddy runs as the `caddy` user, which gets traverse only access to the home folder with
`setfacl -m u:caddy:x /home/gunjan` (static files are written world readable by the container). Request path: internet, Caddy on the host (ports 80 and 443), gunicorn
in `hookd_app_dev` on `127.0.0.1:8000`.

**Static files.** With `DJANGO_DEBUG=false` (and not testing), `STORAGES['staticfiles']` is
`ManifestStaticFilesStorage`: `collectstatic` (run by `hookd.sh serve` on every start) writes content hashed copies of
`backend/static` and the admin assets to `backend/staticfiles`, and `{% static %}` renders the hashed names. Caddy
serves `/static/*` straight from that folder with a one year immutable cache; gunicorn never serves static files. A
`{% static %}` path that does not exist raises an error in production, so reference static files only through the tag.
The dev server and tests use plain `StaticFilesStorage`. Uploads (`backend/media`) are served by Django views, not Caddy.

**Caddy** (`Caddyfile` at the repo root): automatic Let's Encrypt certificates for `hookd.world`, HTTP to HTTPS redirect,
HSTS, gzip/zstd, `www.hookd.world` redirects to `hookd.world`, everything other than `/static/` is reverse proxied to
`127.0.0.1:8000`. Caddy sends `X-Forwarded-Proto`, which `SECURE_PROXY_SSL_HEADER` trusts (only Caddy can reach the
port). With `DJANGO_DEBUG=false` session and CSRF cookies are secure.

First setup on the server:

```bash
apt update && apt install -y git python3 acl caddy   # as root (Caddy from its apt repository)
curl -fsSL https://get.docker.com | sh
timedatectl set-timezone Asia/Kolkata
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
usermod -aG docker gunjan && setfacl -m u:caddy:x /home/gunjan   # then, as gunjan:
cd ~ && git clone https://github.com/Gunjaan/hookd.git && cd hookd
cp .env.example .env        # production values, see below
python3 deploy.py build
python3 deploy.py deploy
docker exec hookd_app_dev python manage.py migrate
docker exec -it hookd_app_dev python manage.py createsuperuser
python3 deploy.py install-cron
sudo ln -sf /home/gunjan/hookd/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

GoDaddy DNS: `A` records for `hookd.world` and `www` pointing at the VPS IP, before Caddy can get certificates.
Production `.env`: `DJANGO_DEBUG=false`, `DJANGO_ALLOWED_HOSTS=hookd.world,www.hookd.world`,
`DJANGO_CSRF_TRUSTED_ORIGINS=https://hookd.world`, `APP_URL=https://hookd.world`, and a fresh `DJANGO_SECRET_KEY`,
database, Redis and encryption values. Add a 2 GB swap file.

Updating: `git pull`, then `python3 deploy.py build` only if `requirements.txt` or the `Dockerfile` changed,
`python3 deploy.py deploy` (restarts gunicorn and Celery, collects static files),
`docker exec hookd_app_dev python manage.py migrate`. A code change without a restart: `docker restart hookd_app_dev`.
Do not use `run` or `stopall` inside the production container: stopping gunicorn stops the container (Docker then
restarts it).

## GoDaddy ANS registration

Hooky is registered and **ACTIVE** on GoDaddy ANS (Agent Name Service, "Agent Identity by GoDaddy") as
`ans://v1.0.0.hookd.world`, agent id `734f2707-6867-457d-abf1-0f0dc6451a62`, agent host `hookd.world`, registered
2026-11-20. Transparency log entry: `https://transparency.ans.godaddy.com/v1/agents/<agentId>`. The agent card is
served by Django (`DEV-agent.md`) and must be live before registering: the registry checks that the card's `skills`
match the registered `functions` (same ids). Registration is done from a shell; the app reads none of it. Docs:
godaddy.com/ans/developers (Quick Start and API Reference).

- **Registry:** `https://api.godaddy.com/v1/agents`. Transparency log (public): `https://transparency.ans.godaddy.com`.
- **Auth:** a GoDaddy Personal Access Token (`gd_pat_...`) from developer.godaddy.com/personal-access-token with the
  `ans.agent-registration:write` permission, sent as `Authorization: Bearer <token>`. Shell only, never in `.env`,
  never committed, never passed as a command line argument (`curl -H @<file>`).
- **Files:** the identity key, CSR, issued identity certificate and request/response JSON are in `~/ans/` on the
  machine that registered (outside the repository, never committed).

Steps (what the live registry actually requires):
1. Identity key and CSR, EC P-256 (RSA is rejected). The SAN needs both the DNS name and the ANS URI:
   ```bash
   mkdir -p ~/ans && chmod 700 ~/ans && cd ~/ans
   openssl ecparam -name prime256v1 -genkey -noout -out identity.key
   openssl req -new -key identity.key -subj "/O=Hookd/CN=hookd.world" -addext "subjectAltName=DNS:hookd.world,URI:ans://v1.0.0.hookd.world" -out identity.csr
   ```
2. Server certificate: GoDaddy's registry does not issue server certificates (`SERVER_CA_DISABLED`), so send the one
   Caddy serves (public, no private key needed):
   `echo | openssl s_client -connect hookd.world:443 -servername hookd.world -showcerts`, first PEM block as
   `serverCertificatePEM`, the rest as `serverCertificateChainPEM`.
3. `POST https://api.godaddy.com/v1/agents/register` with JSON: `agentHost` "hookd.world", `version` "1.0.0",
   `agentDisplayName` "Hooky", `agentDescription` (max 150 characters), `identityCsrPEM`, the two certificate fields,
   and one endpoint: `protocol` "HTTP-API", `transports` ["REST"], `agentUrl` `https://hookd.world/webhooks/instagram/`,
   `metaDataUrl` `https://hookd.world/.well-known/agent-card.json`, `documentationUrl` `https://hookd.world/`,
   `functions` = the card's skills as `{id, name, tags}`. The `202` answer has the `agentId` (in `links`) and an
   `_acme-challenge.hookd.world` TXT record.
4. DNS with the GoDaddy CLI. Always `gddy dns add` (appends), never `gddy dns set` or `delete`: `@` holds the SPF TXT,
   MX and email records. Caddy uses HTTP and TLS-ALPN challenges, so `_acme-challenge` does not affect it.
   ```bash
   gddy dns add hookd.world --type TXT --name _acme-challenge --ttl 600 --data "<challenge value>"
   ```
5. `POST /v1/agents/<agentId>/verify-acme` gives `PENDING_DNS`. `GET /v1/agents/<agentId>` then lists
   `registrationPending.dnsRecords`. The two required ones are published:
   - `_ans.hookd.world` TXT `v=ans1; version=v1.0.0; p=http; mode=direct; url=https://hookd.world/webhooks/instagram/`
   - `_ans-badge.hookd.world` TXT `v=ans-badge1; version=v1.0.0; url=https://transparency.ans.godaddy.com/v1/agents/<agentId>`

   The two optional ones are deliberately not published: an apex `HTTPS` record (it would change how browsers connect
   to hookd.world) and a `_443._tcp` TLSA pin of Caddy's certificate (it would go stale at every Let's Encrypt renewal).
6. `POST /v1/agents/<agentId>/verify-dns` gives `ACTIVE`. Check: `GET /v1/agents?agentHost=hookd.world`.

The `_acme-challenge` TXT record is only needed for validation and can be deleted
(`gddy dns delete hookd.world --type TXT --name _acme-challenge`; no other record uses that name). A new card version
(`DEV-agent.md`) means a new registration with the new version and new `_ans` / `_ans-badge` records. `gddy` itself
has no ANS commands; GoDaddy's `ans-cli` (Homebrew `agentnameservice/ans/ans-cli`) wraps the same endpoints.
