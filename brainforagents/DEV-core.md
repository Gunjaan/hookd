# DEV: Core (`backend/apps/core`)

Shared building blocks. Owns no database tables.

| File | Contents |
|---|---|
| `models.py` | `TimeStampedModel` (abstract): `created_at`, `updated_at` |
| `crypto.py` | Fernet `MultiFernet` built from `FIELD_ENCRYPTION_KEYS`. `encrypt`, `decrypt` (raises `DecryptionError`), `generate_key` |
| `fields.py` | `EncryptedTextField`: encrypts on save, decrypts on load. Cannot be filtered on |
| `exceptions.py` | `HookdError(message=None)`: base for expected, user facing errors. Subclasses set `default_message`. Views catch it and show `message` |
| `model_querysets.py` | `database_is_available()` (`SELECT 1`) |
| `ai.py` | Provider neutral call results: `TokenUsage(model, input_tokens, output_tokens)` (output includes thinking tokens) and `JsonAnswer(data, usage)` |
| `claude.py` | Shared Claude (Anthropic SDK) access for the text engines: `generate_json(model, content, system=, schema=, max_output_tokens=)` returns `JsonAnswer` (needs `ANTHROPIC_API_KEY`, else `ClaudeUnavailable`; 90 s timeout, SDK retries; structured JSON output via `output_config.format` at effort `low`; API status and connection errors, a `refusal` or `max_tokens` stop and a non object answer become `ClaudeError`). Input tokens include cache reads and writes. `image_block(data, mime_type)` builds a base64 image block. Used by `agent.engines.claude` and `creatives.engines.claude` |
| `gemini.py` | Shared Gemini (Google AI Studio) access for the image engine: `generate(model, contents, config)` (needs `GEMINI_API_KEY`, else `GeminiUnavailable`; 90 s timeout; API and network errors become `GeminiError`; keeps the client referenced for the whole call, a temporary `genai.Client` closes when garbage collected), `token_usage(model, response)` from `usage_metadata` (missing counts are 0). Used by `creatives.engines.gemini` |
| `http.py` | JSON helpers for the `/api/` endpoints: `json_ok(**data)`, `json_error(message, status=400)` (`{"error": message}`), `api_login_required` (401 JSON instead of a login redirect) |
| `views.py` | `health`, `landing` (public homepage at `/`, see `DEV-web.md`) |
| `templatetags/hookd_ui.py` | `initials` filter (avatar fallback, see `DEV-web.md`) |
| `management/commands/generate_encryption_key.py` | Prints a new Fernet key |
| `management/commands/cleanup_task_results.py` | Deletes Celery task results older than `CELERY_RESULT_EXPIRES` via the result backend. Daily cron (`DEV-infrastructure.md`) |
| `tests.py` | Health endpoint, landing page (guest and signed in), link preview tags, noindex on signed in pages, encrypt/decrypt round trip, key rotation, unknown key error, Gemini missing key; Claude missing key, non JSON answer, refusal and cut off answers, token usage and request shape on a JSON answer |

## Endpoints

| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/health/` | none | `{"status": "ok", "database": true}` or 503 `degraded` |
| GET | `/` | none | Landing page for everyone |

## Key rotation

Prepend a new key to `FIELD_ENCRYPTION_KEYS`. Old values still decrypt; new writes use the first key.
To re-encrypt everything, load and save each `SocialAccount` (no command for this yet).
