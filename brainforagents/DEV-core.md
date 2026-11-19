# DEV: Core (`backend/apps/core`)

Shared building blocks and the public landing page contact form. Owns no database tables.

| File | Contents |
|---|---|
| `models.py` | `TimeStampedModel` (abstract): `created_at`, `updated_at` |
| `crypto.py` | Fernet `MultiFernet` built from `FIELD_ENCRYPTION_KEYS`. `encrypt`, `decrypt` (raises `DecryptionError`), `generate_key` |
| `fields.py` | `EncryptedTextField`: encrypts on save, decrypts on load. Cannot be filtered on |
| `exceptions.py` | `HookdError(message=None)`: base for expected, user facing errors. Subclasses set `default_message`. Views catch it and show `message` |
| `model_querysets.py` | `database_is_available()` (`SELECT 1`) |
| `http.py` | JSON helpers for the `/api/` endpoints: `json_ok(**data)`, `json_error(message, status=400)` (`{"error": message}`), `api_login_required` (401 JSON instead of a login redirect) |
| `views.py` | `health`, `landing` (public homepage at `/` with an unbound `ContactForm`, see `DEV-web.md`) |
| `forms.py` | `ContactForm` (name, email lowercased, message up to 2000 characters) |
| `services.py` | `client_ip(request)` (last `X-Forwarded-For` entry, which Caddy sets to the real client, else `REMOTE_ADDR`), `send_contact(details, ip)` (at most `CONTACT_LIMIT_PER_HOUR` = 5 per address an hour, counted in the cache under `contact:<ip>`, else `ContactLimitReached`; queues the task), `send_contact_email(details)` (`EmailMessage` to `SUPPORT_EMAIL`, `Reply-To` the sender) |
| `tasks.py` | `send_contact_message(details)`: retries on SMTP and network errors with backoff, like the access request task |
| `api.py` | `contact`: validates `ContactForm`, first field error as `"<Label>: <error>"` (400), limit reached (429), success returns `html` from `core/contact_sent.html` (thank you note that replaces the form) |
| `templates/core/contact_sent.html` | The thank you note shown in place of the contact form |
| `templatetags/hookd_ui.py` | `initials` filter (avatar fallback, see `DEV-web.md`) |
| `management/commands/generate_encryption_key.py` | Prints a new Fernet key |
| `management/commands/cleanup_task_results.py` | Deletes Celery task results older than `CELERY_RESULT_EXPIRES` via the result backend. Daily cron (`DEV-infrastructure.md`) |
| `tests.py` | Health endpoint, landing page (guest and signed in, Try it, tools, contact and video dock), link preview tags, noindex on signed in pages, contact API (email to support with reply to, field error, rate limit per address, POST only, CSRF), encrypt/decrypt round trip, key rotation, unknown key error |

## Endpoints

| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/health/` | none | `{"status": "ok", "database": true}` or 503 `degraded` |
| GET | `/` | none | Landing page for everyone |
| POST | `/api/contact/` (`api-contact`) | none, CSRF | Landing page contact form: emails `SUPPORT_EMAIL` through Celery. `{html}` thank you note, or `{error}` (400 invalid, 429 over the hourly limit) |

## Key rotation

Prepend a new key to `FIELD_ENCRYPTION_KEYS`. Old values still decrypt; new writes use the first key.
To re-encrypt everything, load and save each `SocialAccount` (no command for this yet).
