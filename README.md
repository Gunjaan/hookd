# Hookd

Every video gets its own AI manager, in your language.

Django with server rendered templates. Three Docker containers: the app (you start and work inside it), Postgres and Redis.
Current scope: creator signup/login and Instagram account connection.
Detailed docs for every feature: [`brainforagents/`](brainforagents/DEV-overview.md). Rules for contributors and agents: [`AGENTS.md`](AGENTS.md).

Hooky, the reply agent, is a verified agent on GoDaddy's Agent Name Service (Agent Identity by GoDaddy):
[`ans://v1.0.0.hookd.world`](https://transparency.ans.godaddy.com/v1/agents/734f2707-6867-457d-abf1-0f0dc6451a62),
with its agent card at [hookd.world/.well-known/agent-card.json](https://hookd.world/.well-known/agent-card.json).

Live at [hookd.world](https://hookd.world). Videos: [product demo](https://www.youtube.com/watch?v=7HOha1_jlVM) and [how to connect Instagram](https://youtu.be/lWhaOPulgSA).
How it was built, with GoDaddy and Claude: [`BUILDING-HOOKD.md`](BUILDING-HOOKD.md).

## For the judges

Hi, I am Gunjan, and I built Hookd. A few things before you try it:

- **Demo login credentials:** the username and password are in the pitch deck uploaded to HackerEarth as an additional
  file (slide 14). For privacy and to prevent abuse, I am not putting them in this public repository. If you cannot find
  them, email me at **gunjanverma0133@gmail.com**.
- **My Meta app review is pending.** I applied on 15 November 2026 and Meta usually takes about 2 weeks. Until it is
  approved, Instagram login works only for accounts added as testers on my Meta app, so a new Instagram account cannot
  connect yet. Email me and I will add you as a tester, or you can use my demo account that is already connected.
- **Try Hooky without logging in:** comment on or DM my demo shop [@while.wanderingg](https://www.instagram.com/while.wanderingg/)
  on Instagram and watch the agent reply.

## Quick start

```bash
cp .env.example .env
```

Fill `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD` and `FIELD_ENCRYPTION_KEYS` (generate a key with `python manage.py generate_encryption_key` inside the container, then restart it).

```bash
python3 deploy.py build
```

```bash
python3 deploy.py run
```

`run` first starts the Postgres and Redis containers if they are not running; they keep running in the
background with their data in Docker volumes.

You are now in a bash shell inside the container, in `backend/`. First time only:

```bash
python manage.py migrate
```

Start the server:

```bash
run
```

Or `runbg` to start it in a detached screen session. Open http://localhost:8000. Django admin is at
http://localhost:8000/admin (create a user with `python manage.py createsuperuser`).
Another shell in the same container: `docker exec -it hookd_app_dev bash`.

## Scheduled jobs

```bash
python3 deploy.py install-cron
```

## Production

`python3 deploy.py deploy` starts the app detached (gunicorn and Celery) behind Caddy (`Caddyfile`), which serves
HTTPS and static files. Server setup and updates: [`brainforagents/DEV-infrastructure.md`](brainforagents/DEV-infrastructure.md#production-godaddy-vps-caddy).

## Connecting Instagram

Set `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` (Meta app), `INSTAGRAM_LOGIN_CONFIG_ID` and an HTTPS `INSTAGRAM_REDIRECT_URI` in `.env`.
Meta app setup and local tunnel steps: [`brainforagents/DEV-instagram.md`](brainforagents/DEV-instagram.md).

## Tests (inside the container)

```bash
python manage.py test
```
