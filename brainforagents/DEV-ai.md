# DEV: AI calls (`backend/apps/ai`)

One place for every AI provider request: the Claude and Gemini clients, and a record of each call (`AiCall`) with
what was sent, what came back, tokens, cost, duration, and who and what it was for. Admin only; sellers never see it.

## How a call is recorded

1. A feature service wraps its engine call in `services.attributed(purpose, user=, account=, subject=)` (a context
   manager over a `ContextVar`; the user defaults to the account's owner). Engines and their interfaces know nothing
   about it.
2. The engine calls the client (`claude.generate_json` or `gemini.generate`).
3. The client calls `services.record(...)` once per request that reached the API: after a usable answer (`ok`), and
   also after an API or network error, a refusal, a cut off answer or unreadable JSON (`failed`, with `error`, and the
   tokens when the provider billed them). A missing key raises before any request and records nothing.
4. `record` reads the current attribution (outside `attributed` the purpose is `other`, no user or subject), prices the
   call (`call_cost`) and stores it through `model_querysets.add_call`, inside a savepoint. A failure to store is
   logged and never breaks the AI call.

Image bytes are never stored: images in the request (and Gemini's returned photo) are kept as
`{"type": "image", "media_type", "bytes"}` (`media_summary`).

Call sites and their purposes:

| Purpose | Where | user / account / subject |
|---|---|---|
| `creative_plan` | `creatives.services.generate_creative` | draft owner / none / the `Creative` |
| `creative_photo` | `creatives.services.render_slide` | draft owner / none / the `Creative` |
| `caption_rewrite` | `creatives.services.rewrite_caption` | the user / none / the `Creative` |
| `comment_agent` | `agent.loop.advance`, one record per model step | account owner / the run's account / the `Comment`, or the `AgentRun` for a DM started run |
| `comment_reply` | Older calls only (the single call reply engine before the agent loop) | account owner / the account / the `Comment` |
| `reply_preview` | `agent.services.preview_reply` | account owner / the account / the `Post` or none |
| `dm_filter` | `agent.services._starts_run` | account owner / the account / the `DirectMessage` |
| `learn_caption` | `agent.services.learn_from_post` | account owner / the post's account / the `Post` |
| `learn_reply` | `agent.services.learn_from_reply` | account owner / the comment's account / the `CommentReply` |

A new AI call site wraps its engine call in `attributed` with a new `Purpose` value.

## Files

| File | Contents |
|---|---|
| `constants.py` | `Provider` (`claude`, `gemini`), `Purpose` (table above, plus `other`), `CallStatus` (`ok`, `failed`) |
| `models.py` | `AiCall` |
| `model_querysets.py` | `add_call(**fields)` (savepoint), `usage_totals(queryset)` (input, output tokens and cost), `with_usage_totals(queryset)` (annotates rows of any model with `total_tokens` and `total_cost` over the calls about them, by subquery), `calls_about(obj)` |
| `services.py` | `attributed`, `record`, `call_cost(model, input_tokens, output_tokens)` (USD at `AI_MODEL_PRICES` per million tokens, 6 decimals; a model without a price costs 0 and logs a warning), `media_summary(mime_type, data)` |
| `claude.py` | `generate_json(model, content, system=, schema=, max_output_tokens=)` returns the answer `dict` (needs `ANTHROPIC_API_KEY`, else `ClaudeUnavailable`; 90 s timeout, SDK retries; structured JSON output via `output_config.format` at effort `low`; API status and connection errors, a `refusal` or `max_tokens` stop and a non object answer become `ClaudeError`). Input tokens include cache reads and writes. `run_step(model, system=, tools=, messages=, max_output_tokens=)` is one agent loop step on the beta Messages API with `drop_block` thinking binding (see `DEV-agent.md`), returning the content blocks and stop reason. `image_block(data, mime_type)` builds a base64 image block. Used by `agent.engines.claude` and `creatives.engines.claude` |
| `gemini.py` | `generate(model, contents, config)` returns the SDK response (needs `GEMINI_API_KEY`, else `GeminiUnavailable`; 90 s timeout; API and network errors become `GeminiError`; keeps the client referenced for the whole call, a temporary `genai.Client` closes when garbage collected). Output tokens include thinking tokens. Used by `creatives.engines.gemini` |
| `admin.py` | `AiCallAdmin` and `calls_link(obj)` |
| `templates/admin/ai/aicall/change_list.html` | Totals line over the filtered calls |
| `migrations/0002_copy_creative_ai_calls.py` | Copied the post studio's earlier `creatives.CreativeAiCall` rows (tokens and cost only) before that table was dropped |
| `tests.py` | Claude: missing key records nothing, answer recorded with request, response, tokens (cache reads as input) and cost, images stored as type and size, unusable answers and API errors recorded as failed, attribution and its end, unknown model costs 0, a failed record never breaks the call. Gemini: missing key, call recorded without image bytes, API error recorded. Admin totals, subject filter and detail page |

## Model

| Model | Fields |
|---|---|
| `AiCall` | `created_at`, `provider`, `model`, `purpose`, `status`, `error`, `user` (SET_NULL), `account` (`integrations.SocialAccount`, SET_NULL), `subject` (generic: `subject_type`, `subject_id`; no cascade, so calls outlive what they were about), `system` (system prompt), `request` JSON (Claude: `content`, `max_output_tokens`; Gemini: `contents`, `config`), `response` JSON (Claude: `stop_reason`, `text`; Gemini: `parts`; empty on an API error), `input_tokens`, `output_tokens`, `cost_usd` (at the prices of that moment), `duration_ms`. Property `total_tokens`. Newest first |

## Admin

"Ai calls": filters by purpose, provider, model, status, user, subject type and date, with a totals line for the
filtered calls; read only, the detail page shows the full system prompt, request and response. `?subject_type__id__exact=&subject_id__exact=`
lists the calls about one object (`calls_link` builds it). The `Creative` admin list shows tokens and cost per draft
(`with_usage_totals`) and links to its calls.

## Settings

| Setting | Notes |
|---|---|
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | Provider keys (`DEV-infrastructure.md`) |
| `AI_MODEL_PRICES` | USD per million input and output tokens per model name. Update it with the model settings |
