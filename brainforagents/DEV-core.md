# DEV: Core (`backend/apps/core`)

Shared building blocks. Owns no database tables.

| File | Contents |
|---|---|
| `models.py` | `TimeStampedModel` (abstract): `created_at`, `updated_at` |
| `crypto.py` | Fernet `MultiFernet` built from `FIELD_ENCRYPTION_KEYS`. `encrypt`, `decrypt` (raises `DecryptionError`), `generate_key` |
| `fields.py` | `EncryptedTextField`: encrypts on save, decrypts on load. Cannot be filtered on |
| `exceptions.py` | `HookdError(message=None)`: base for expected, user facing errors. Subclasses set `default_message`. Views catch it and show `message` |
| `model_querysets.py` | `database_is_available()` (`SELECT 1`) |
| `gemini.py` | Shared Gemini (Google AI Studio) access for the AI engines: `generate(model, contents, config)` (needs `GEMINI_API_KEY`, else `GeminiUnavailable`; 90 s timeout; API and network errors become `GeminiError`; keeps the client referenced for the whole call, a temporary `genai.Client` closes when garbage collected), `generate_json(model, contents, system=, schema=, max_output_tokens=)` (JSON schema answer, `GeminiError` when not a JSON object). Used by `agent.engines.gemini` and `creatives.engines.gemini` |
| `views.py` | `health`, `landing` (public homepage at `/`, see `DEV-web.md`) |
| `templatetags/hookd_ui.py` | `initials` filter (avatar fallback, see `DEV-web.md`) |
| `management/commands/generate_encryption_key.py` | Prints a new Fernet key |
| `management/commands/cleanup_task_results.py` | Deletes Celery task results older than `CELERY_RESULT_EXPIRES` via the result backend. Daily cron (`DEV-infrastructure.md`) |
| `tests.py` | Health endpoint, landing page (guest and signed in), encrypt/decrypt round trip, key rotation, unknown key error, Gemini missing key and non JSON answer |

## Endpoints

| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/health/` | none | `{"status": "ok", "database": true}` or 503 `degraded` |
| GET | `/` | none | Landing page for everyone |

## Key rotation

Prepend a new key to `FIELD_ENCRYPTION_KEYS`. Old values still decrypt; new writes use the first key.
To re-encrypt everything, load and save each `SocialAccount` (no command for this yet).
