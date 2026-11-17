# DEV: Reply agent (`backend/apps/agent`)

Answers new comments on connected accounts from an account brain and a per post video brain. There are no rules to
set up: when the brains hold the answer the reply goes out on its own, when a needed fact is missing the draft waits in
the inbox for approval. The engine that writes replies is pluggable; today it is a **low cost Gemini model** (`GeminiReplyEngine`)
until Claude is connected, with the word matching **simulated engine** standing in when `GEMINI_API_KEY` is empty and
in tests. Brains are filled by the creator on the agent page and the post page, and by the post studio's "Teach your
reply agent" questions (`DEV-creatives.md`). Sending is slow and capped on purpose so Hookd stays inside Meta's rate limits and anti-spam rules.

## Flow

1. A webhook comment is stored by `comments.services.record_incoming_comments`, which queues
   `tasks.handle_comment(comment_id)` on commit for every new comment not written by the account itself.
   Comments loaded in the studio are history and never queue the agent.
2. `services.handle_new_comment` ignores the account's own comments. Otherwise it first calls
   `content.services.ensure_post`, which fetches and stores the post when it is unknown (posted outside Hookd, since
   Instagram has no new post webhook), even when the agent is off. It then ignores the comment (no row) when the agent
   is off for the account, a reply already exists, or the platform has no `public_reply` capability. It stores a
   `skipped` reply when the post's video brain turns the agent off, the comment is a thread reply and
   `reply_to_threads` is off, or the comment is older than `MAX_COMMENT_AGE_HOURS`.
3. It builds an `AgentContext` (comment, voice, account brain, post caption, video brain) and calls
   `engines.registry.get_engine().decide(context)`.
4. `_route` turns the decision into a status (first match wins):

   | Condition | Status |
   |---|---|
   | Engine intent is `spam` | `skipped` |
   | `provider.reply_text_problems(text)` is not empty | `pending_review` (reason = the problems) |
   | Confidence below `AUTO_REPLY_MIN_CONFIDENCE`: the brains do not hold a needed fact | `pending_review` ("Missing information: " + engine reason) |
   | The same text (leading @mention ignored) went out `MAX_SAME_TEXT_PER_HOUR` times in the last hour | `pending_review` |
   | Otherwise | `queued` via `schedule_reply` |

5. `schedule_reply(reply, not_before=None)` locks the `AgentProfile` row, takes the slot
   `max(now, next_send_at, paused_until, not_before)`, moves `next_send_at` forward by `MIN_SPACING_SECONDS` plus a
   random 0 to `JITTER_SECONDS`, marks the reply `queued` with `scheduled_for`, and on commit queues
   `tasks.send_reply` with `eta=slot`.
6. `send_reply(reply_id)` returns early if the reply is not queued or its slot is more than 5 s away (`not_due`, so a
   redelivered Celery task cannot jump the queue), then claims it (`queued -> sending`, a conditional update that only
   one caller can win). Then: account not active -> `failed`; account paused -> rescheduled after the pause;
   hourly (`MAX_PER_HOUR`) or daily (`MAX_PER_DAY`) cap reached -> rescheduled for when the oldest send in the window
   ages out; text problems -> `pending_review`; else `provider.reply_to_comment`.
   Results: success `sent`; `RateLimitedError` pauses the account for `RATE_LIMIT_PAUSE_MINUTES` (`paused_until`,
   `pause_reason`) and requeues; `TokenExpiredError` marks the account `expired` and the reply `failed`; other
   `ProviderAPIError` -> `failed` with the message.
7. The creator works the inbox: approve (optionally edited, checked with `reply_text_problems`) -> `schedule_reply`,
   discard -> `rejected`, try again on a failed reply -> `schedule_reply`.

## Meta compliance (why the limits exist)

- Replies only to new comments people wrote on the creator's own posts, never to the account's own comments, and no
  thread replies unless the creator turns them on.
- Replies go out one at a time per account, 45 to 75 s apart, max 20 an hour and 150 a day (`AGENT_REPLY_LIMITS` in
  settings). Repeated identical text is held for review because Instagram flags copy-paste replies as spam.
- Graph rate limit and policy block errors (codes 4, 17, 32, 613, 368, HTTP 429) pause the account for an hour instead
  of retrying.
- Instagram text rules are checked before sending (`DEV-instagram.md`).
- The agent starts **off** per account, so nothing is posted until the creator turns it on. Once on, only replies the
  engine is confident the brains fully answer go out unasked.

Engine failure: when `decide` raises a `HookdError` (Gemini down, missing key, unreadable answer),
`handle_new_comment` stores the reply as `pending_review` with an empty draft and the error in `decision.reason`, so the
creator writes it in the inbox. The preview page shows the error as a message.

## Settings (`config/settings.py`)

| Setting | Default | Notes |
|---|---|---|
| `HOOKD_REPLY_ENGINE` | `apps.agent.engines.gemini.GeminiReplyEngine` when `GEMINI_API_KEY` is set and not testing, else `apps.agent.engines.simulated.SimulatedReplyEngine` | Dotted path to a `ReplyEngine` |
| `AGENT_REPLY_MODEL` | env `GEMINI_REPLY_MODEL`, else `gemini-3.5-flash-lite` | Text model for `GeminiReplyEngine` |
| `AGENT_REPLY_LIMITS` | see below | One dict, read by `services._limit` |

`AGENT_REPLY_LIMITS`: `MIN_SPACING_SECONDS` 45, `JITTER_SECONDS` 30, `MAX_PER_HOUR` 20, `MAX_PER_DAY` 150,
`MAX_SAME_TEXT_PER_HOUR` 3, `MAX_COMMENT_AGE_HOURS` 24,
`AUTO_REPLY_MIN_CONFIDENCE` 0.75, `RATE_LIMIT_PAUSE_MINUTES` 60.

## Engines (`engines/`)

- `base.py`: `AgentContext(comment_text, author_username, account_username, voice, account_knowledge, post_caption,
  post_knowledge)`, `AgentDecision(intent, language, reply_text, confidence, reason)`, `ReplyEngine` ABC (`name`,
  `decide(context)`). Engines never send; their confidence decides between sending and review.
- `simulated.py`: `SimulatedReplyEngine`. Reply = `@author ` + the brain line (video brain first, then account brain)
  sharing the most words with the comment (stop words dropped, plural "s" ignored), confidence 0.9. No such line and
  the comment has a "?": holding reply, confidence 0.3 (goes to review). Otherwise "Thank you so much!", 0.8.
  Ignores `voice`; language is always `unknown`.
- `prompts.py`: `REPLY_SYSTEM` (constant, cache friendly): says confident replies are posted unchecked; reply in the
  commenter's language and script, starts with `@username`, 1 or 2 sentences under 200 characters, follows the voice;
  only given facts, never invented prices, stock or links; options not in a listed set get the available ones; never
  claims an action (no "we DMed you"), may invite a DM only when the facts say so; confidence 0.8+ only when the facts
  fully answer or no facts are needed (praise, thanks); missing fact gives a holding reply with confidence at most
  0.4 and the fact named in `reason`; spam gets intent `spam` and an empty reply. `REPLY_SCHEMA` (intent, language,
  reply_text, confidence, reason). `reply_request(context)`: compact JSON with empty fields dropped and the caption
  cut to 1000 characters.
- `gemini.py`: `GeminiReplyEngine` (`name = "gemini"`): one `core.gemini.generate_json` call, max 400 output tokens.
  Confidence is clamped to 0..1 (unreadable is 0).
- `registry.py`: `get_engine()`.

**Plugging in Claude:** add `engines/claude.py` with a `ReplyEngine` that sends `prompts.REPLY_SYSTEM` (cached) and
`prompts.reply_request(context)` and returns an `AgentDecision`, set `HOOKD_REPLY_ENGINE`, add the API key env var to
`.env.example` and `DEV-infrastructure.md`. Routing and sending need no change.

## Models

| Model | Fields |
|---|---|
| `AgentProfile` (one per `SocialAccount`, `related_name="agent_profile"`, created lazily) | `enabled` (False), `voice`, `knowledge` (account brain, one fact per line), `reply_to_threads` (False), `paused_until`, `pause_reason`, `next_send_at` |
| `PostBrain` (one per `content.Post`, `related_name="brain"`) | `knowledge` (video brain), `agent_enabled` (True) |
| `CommentReply` (one per `comments.Comment`, `related_name="agent_reply"`) | `status` (`pending_review`, `queued`, `sending`, `sent`, `skipped`, `rejected`, `failed`), `draft_text` (engine output), `final_text` (what is sent), `decision` JSON (`intent`, `language`, `confidence`, `engine`, `engine_reason`, `reason`), `scheduled_for`, `sent_at`, `reply_external_id`, `error`, `attempts` |

Choices in `constants.py`: `ReplyStatus`. All in the Django admin.
A video brain only exists for posts Hookd has synced (`Post` rows); comments on other posts use the account brain.

## Services (`services.py`)

| Function | Does |
|---|---|
| `agent_accounts`, `get_profile`, `update_profile`, `is_paused`, `sending_limits`, `supports_replies` | Settings page |
| `get_post_brain`, `save_post_brain`, `preview_posts` | Video brain, preview post choices (20 newest) |
| `handle_new_comment(comment_id)` | Steps 2 to 4. Returns the outcome |
| `preview_reply(account, text, post)` | Same context and routing (no repeated text check), stores and sends nothing. Returns `Preview(decision, outcome, reason)` |
| `schedule_reply`, `send_reply`, `send_overdue_replies` | Steps 5 and 6; recovery sends queued replies more than 5 minutes past their slot |
| `inbox`, `inbox_counts`, `get_user_reply` (`ReplyNotFound`), `approve_reply`, `reject_reply`, `retry_reply` | Inbox. Wrong status raises `ReplyNotEditable`, refused text `ReplyTextRejected` |
| `pending_review_count(user)` | Sidebar badge (context processor `context_processors.agent_nav`) |

`model_querysets.py`: profile (`get_profile`, `get_or_create_profile`, `lock_profile`, `update_profile`), brain (`get_post_brain`, `save_post_brain`), replies (`reply_exists`, `create_reply_if_new`, `get_reply`,
`get_reply_for_user`, `update_reply`, `claim_reply_for_sending`, `sent_times_since`,
`outgoing_texts_since`, `list_replies_for_user`, `reply_counts_for_user`,
`pending_review_count`, `overdue_queued_reply_ids`). Uses `content.model_querysets.find_post`/`latest_posts` and
`comments.model_querysets.get_comment`.

## Tasks and command

`tasks.py`: `handle_comment(comment_id)`, `send_reply(reply_id)`; both return the outcome string.
`send_due_replies` management command: cron every 10 minutes (`DEV-infrastructure.md`).

## Pages and JSON actions (`views.py`, `api.py`, `urls.py`, `forms.py`, `templates/agent/`)

Pages are GET only. Every action is a `<form data-api>` that posts with `fetch` to `api.py` (`DEV-web.md`): invalid
forms and `HookdError` answer 400 `{"error"}` (shown as a message), other users' objects 404, logged out 401.

| Route | Name | Page |
|---|---|---|
| `GET /agent/` | `agent` | First account's agent page, or connections |
| `GET /agent/accounts/<id>/` | `agent-account` | One `AgentSettingsForm` (initial from the profile, saved by `api-agent-settings`) in three numbered steps: 1 "Turn on auto replies" (`enabled` checkbox, help text explains that replies the brains answer go out on their own and the rest wait for approval), 2 "Replies sound like you" (voice, account brain as "What should the agent know?", thread replies), 3 safe sending limits in a `<details>`. Pause warning on top |
| `GET /agent/accounts/<id>/preview/` | `agent-preview` | `PreviewForm`: comment and optional post. `api-agent-preview` fills `#preview-result` with outcome, confidence and reply |
| `GET /agent/inbox/?status=` | `agent-inbox` | Tabs: Needs review, Scheduled, Sent, Skipped, Failed, Rejected (100 newest each, `views.inbox_tabs`). Review items have an editable reply, "Approve and send" and "Discard"; failed ones "Try again" |
| `POST /api/agent/accounts/<id>/settings/` | `api-agent-settings` | `update_profile(**cleaned_data)`, `{message}` |
| `POST /api/agent/accounts/<id>/preview/` | `api-agent-preview` | `preview_reply`, `{html}` from `preview_result.html`. Engine errors are a 400 with the reason |
| `POST /api/agent/posts/<id>/brain/` | `api-agent-post-brain` | `PostBrainForm` from the post page's "Video brain" card, `{message}` |
| `POST /api/agent/replies/<id>/approve/`, `/reject/`, `/retry/` | `api-reply-approve`, `-reject`, `-retry` | `{html: "", fragments, message}`: the card is removed, `fragments` redraws `#inbox-tabs` (counts), `#nav-replies-count` (sidebar badge) and, when the tab is now empty, `#inbox-list` |

Templates: `account.html`, `preview.html`, `preview_result.html`, `inbox.html`, `inbox_tabs.html`, `inbox_list.html`;
the sidebar badge is `components/replies_badge.html`.

## Tests (`tests.py`)

Webhook comment queued once, own comment not queued; unknown post fetched for the agent, stored even with the agent off, fetch failure decides without it; agent off stores nothing; thread, old and post-off comments
skipped; spam skipped; video brain answer sent on its own (Graph call checked); praise thanked on its own; missing
information goes to review; the same person can get several replies; repeated text; refused text; spacing
45 to 75 s; hourly cap reschedules without a call; rate limit pauses and requeues; expired token; never sent twice;
early task waits; `send_due_replies`; Instagram text rules; inbox list, approve edited text (JSON with redrawn tabs,
empty list and badge), approve refused text, reject, retry, sidebar count; settings save and on/off checkbox, home redirect, video brain save, preview stores
nothing, 404 for other users. Gemini engine (model call mocked): decision mapped and clamped, compact request without
empty fields or rules, unreadable confidence is 0, constant system prompt without `rule_id` in the schema, engine
failure goes to review with the reason, preview shows the error.
