# AGENTS.md

Instructions for every AI agent (and human) working in this repository.

## 1. Read the brain first

`brainforagents/` holds one `DEV-<feature>.md` per feature. Together they describe the whole codebase:
what exists, where it lives, how data flows, every endpoint, every env var and every command.

- **Before touching code, read `brainforagents/DEV-overview.md`, then the DEV file for the feature you are changing.**
  You should rarely need to open source files just to understand how something works.
- **After every change, update the matching DEV file in the same change.** New feature means new
  `DEV-<feature>.md` plus a row in the index in `DEV-overview.md`. A DEV file that disagrees with the code is a bug.
- DEV files describe the current state, not history. Remove things that no longer exist.

## 2. Project rules

- **Stack:** Django in `backend/`. Plain HTML, CSS and JS: server rendered Django templates with session auth, plus
  `fetch` to JSON endpoints. No JS framework (no Vue, React...) and no build step.
- **No em dashes** anywhere: code, comments, UI copy, docs, commit messages. Use a colon, comma, or hyphen.
- **Professional code, no filler.** No placeholder features, no commented out code, no speculative abstractions.
  Delete code, settings, dependencies and docs that nothing uses any more, unless the user says to keep them.
- **Secrets only in env vars.** Never commit `.env`, keys, or tokens. Add every new variable to `.env.example`
  and to `brainforagents/DEV-infrastructure.md`.
- **Data:** Postgres in its own container (`hookd_postgres`). Redis in its own container (`hookd_redis`) is the Celery
  broker (database 0) and Django's cache (database 1).
- **Background work:** Celery tasks live in `apps/<name>/tasks.py` and only call a service function. Queue them with
  `transaction.on_commit` when they depend on rows written in the same request.
- **Scheduled work:** host cron runs Django management commands via `docker exec` (`MANAGE_CRON_JOBS` in `deploy.py`,
  installed with `python3 deploy.py install-cron`). No celery beat.

### Django conventions

- Boilerplate is generated, never hand written: `django-admin startproject`, `python manage.py startapp <name> apps/<name>`.
  Then set `name = 'apps.<name>'` in the generated `apps.py`.
- **Every app has one `model_querysets.py`. It is the only place that touches the ORM**
  (`Model.objects.filter/get/create/update_or_create/delete`, raw cursors). Views, services, forms,
  commands and tasks import functions from it.
- Business logic lives in `services.py`. Views stay thin: validate input, call a service, render a template or redirect.
- Domain errors subclass `apps.core.exceptions.HookdError` with a user facing `default_message`; views catch it
  and show `exc.message` (Django messages or the page itself).
- Platform code goes through the provider interface in `apps/integrations/providers/`. Never branch on a
  platform name outside a provider package.

### Template and CSS conventions

- Shared templates in `backend/templates/` (`base.html`, `layouts/`, `components/`); page templates in
  `apps/<name>/templates/<name>/`. Views live in `views.py` / `urls.py`.
- Use the design tokens in `backend/static/css/tokens.css` and the classes and partials listed in
  `brainforagents/DEV-web.md`. No raw colors, font sizes, or spacing values in CSS or templates.
- JavaScript is assumed. Lazy loading, polling and in-page actions call JSON endpoints with `fetch` (the `api()` helper
  in `static/js/app.js`), never fetch and parse a full page. JSON views live in `apps/<name>/api.py` under `/api/...`
  URLs, use `apps.core.http` (`api_login_required`, `json_ok`, `json_error`) and keep session auth and CSRF. Rich
  repeated markup comes back as an `html` snippet rendered from the same partial the page uses; simple values come
  back as plain data.

## 3. Running things

Three containers share the `hookd_net` network: `hookd_postgres` and `hookd_redis` run detached and stay up, and the
app container (`hookd_app_dev`) is started by `python3 deploy.py run` (which starts the other two first if needed). It
opens a shell where the user starts the app with `run` (or `runbg` in a detached screen): the Celery worker, then the
Django dev server. Running `run` again stops the old processes first, so there is always one of each.
`restart-celery` restarts the worker, `stopall` stops everything. Full details in `brainforagents/DEV-infrastructure.md`.

- Run commands in the already running container: `docker exec hookd_app_dev python manage.py test`
  (workdir is `backend/`).
- If `hookd_app_dev` is not running, report that and stop. Do not start it yourself; `deploy.py run` is interactive.
- Never use the host's Python for project commands.

Before finishing any change: backend tests pass (`manage.py test`), `manage.py check` is clean, DEV docs updated.
