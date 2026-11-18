# DEV: Reply agent (`backend/apps/agent`)

Answers new comments on connected accounts from an account brain and a per post video brain. There are no rules to
set up: when the brains hold the answer the reply goes out on its own, when a needed fact is missing the draft waits in
the inbox for approval. The engine that writes replies is pluggable; today it is **Claude Haiku** (`ClaudeReplyEngine`), with the
word matching **simulated engine** standing in when `ANTHROPIC_API_KEY` is empty and
in tests. Brains are filled by the creator on the agent page and the post page, by the post studio's "Teach your
reply agent" questions (`DEV-creatives.md`), and **on their own** by the brain learner: a post's caption (read once, on
the first comment or when Hookd publishes it) and every inbox reply the creator rewrote become learned facts (see "Learned brain"). Sending is slow and capped on purpose so Hookd stays inside Meta's rate limits and anti-spam rules.

## Flow

1. A webhook comment is stored by `comments.services.record_incoming_comments`, which queues
   `tasks.handle_comment(comment_id)` on commit for every new comment not written by the account itself.
   Comments loaded in the studio are history and never queue the agent.
2. `services.handle_new_comment` ignores the account's own comments. Otherwise it first calls
   `content.services.ensure_post`, which fetches and stores the post when it is unknown (posted outside Hookd, since
   Instagram has no new post webhook), even when the agent is off, and reads the post's caption once if it never
   did (`learn_from_post`, synchronous so this reply can use the facts; also with the agent off). It then ignores the comment (no row) when the agent
   is off for the account, a reply already exists, or the platform has no `public_reply` capability. It stores a
   `skipped` reply when the post's video brain turns the agent off, the comment is a thread reply and
   `reply_to_threads` is off, or the comment is older than `MAX_COMMENT_AGE_HOURS`.
3. It builds an `AgentContext` (comment, voice, account brain, post caption, video brain; each brain is the
   creator's text plus the learned facts, see "Learned brain") and calls
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

## Learned brain

The agent builds its own memory from what goes through Hookd, stored as `LearnedFact` rows next to (never inside)
what the creator typed.

| Source | Trigger | What is learned |
|---|---|---|
| Post caption, first comment | `handle_new_comment` calls `learn_from_post` for the comment's post (stored, or just fetched by `ensure_post`) | `learn_from_post`: claims the post (`claim_caption_learning`: a conditional update that sets `PostBrain.caption_learned_at` only while it is empty, so only one caller ever wins), then `learner.from_caption(caption)`. Once claimed it never reads that post again (`already_learned`), even if the caption changes. An empty caption learns nothing but stays claimed. A learner error releases the claim (`release_caption_learning`), logs and returns `failed`, so the next comment retries |
| Post caption, published from Hookd | `receivers.learn_from_published_caption` on `content.signals.post_published`: queues `tasks.learn_from_post` on commit | Same as above |
| Inbox reply | `approve_reply` when `CommentReply.written_by_creator` (the sent text differs from the draft, leading @mention ignored): queues `tasks.learn_from_reply` on commit | `learn_from_reply`: `learner.from_reply(question, answer, post_caption)`, the facts the creator's answer states, linked to the reply (`source_reply`) |

Each fact has a scope: `post` facts go on the post (`LearnedFact.post`), `account` facts (shipping, returns, how to
order) are shop wide (`post` empty); a fact without a stored post is shop wide. Saving replaces older learned facts on
the same topic: same place (that post, or shop wide) and same label, case insensitive (`save_learned_facts`).

Using them: `_context` loads up to `LEARNED_FACTS_LIMIT` (40) newest shop facts and post facts and appends them as
`Label: text` lines after the creator's brain (`_brain_text`). A learned fact whose label matches a creator line
(`Label: ...`) is left out, so what the creator typed always wins. The preview uses the same context.

Captions answer questions directly too: the whole caption (up to 2200 characters) is in every reply request, and
`REPLY_SYSTEM` tells the engine the caption's prices, sizes, offers and dates are usable facts (post facts win on a
conflict).

UI: the agent page's "What Hooky learned" card lists shop facts and counts post facts; the post page and post modal list
the post's facts under the Video brain. Each fact has Remove (`api-agent-fact-forget`).

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

Engine failure: when `decide` raises a `HookdError` (Claude down, missing key, refusal, unreadable answer),
`handle_new_comment` stores the reply as `pending_review` with an empty draft and the error in `decision.reason`, so the
creator writes it in the inbox. The preview page shows the error as a message.

## Settings (`config/settings.py`)

| Setting | Default | Notes |
|---|---|---|
| `HOOKD_REPLY_ENGINE` | `apps.agent.engines.claude.ClaudeReplyEngine` when `ANTHROPIC_API_KEY` is set and not testing, else `apps.agent.engines.simulated.SimulatedReplyEngine` | Dotted path to a `ReplyEngine` |
| `AGENT_REPLY_MODEL` | env `CLAUDE_REPLY_MODEL`, else `claude-haiku-5-5` | Model for `ClaudeReplyEngine` and `ClaudeBrainLearner` |
| `HOOKD_BRAIN_LEARNER` | `apps.agent.engines.claude.ClaudeBrainLearner` when `ANTHROPIC_API_KEY` is set and not testing, else `apps.agent.engines.simulated.SimulatedBrainLearner` | Dotted path to a `BrainLearner` |
| `AGENT_REPLY_LIMITS` | see below | One dict, read by `services._limit` |

`AGENT_REPLY_LIMITS`: `MIN_SPACING_SECONDS` 45, `JITTER_SECONDS` 30, `MAX_PER_HOUR` 20, `MAX_PER_DAY` 150,
`MAX_SAME_TEXT_PER_HOUR` 3, `MAX_COMMENT_AGE_HOURS` 24,
`AUTO_REPLY_MIN_CONFIDENCE` 0.75, `RATE_LIMIT_PAUSE_MINUTES` 60.

## Engines (`engines/`)

- `base.py`: `AgentContext(comment_text, author_username, account_username, voice, account_knowledge, post_caption,
  post_knowledge)`, `AgentDecision(intent, language, reply_text, confidence, reason)`, `ReplyEngine` ABC (`name`,
  `decide(context)`). Engines never send; their confidence decides between sending and review. `LearnedFactDraft(scope, label, text)`
  and the `BrainLearner` ABC (`name`, `from_caption(caption)`, `from_reply(question, answer, post_caption)`).
- `simulated.py`: `SimulatedReplyEngine`. Reply = `@author ` + the brain line (video brain first, then account brain)
  sharing the most words with the comment (stop words dropped, plural "s" ignored), confidence 0.9. No such line and
  the comment has a "?": holding reply, confidence 0.3 (goes to review). Otherwise "Thank you so much!", 0.8.
  Ignores `voice`; language is always `unknown`. `SimulatedBrainLearner`: caption sentences (split on `.!?` and new
  lines, hashtags and @mentions dropped) that contain a digit become post facts labelled `Caption` (at most 8); a
  rewritten reply becomes one post fact labelled with the question, text = the answer without the @mention.
- `prompts.py`: `REPLY_SYSTEM` (constant, cache friendly): says confident replies are posted unchecked; reply in the
  commenter's language and script, starts with `@username`, 1 or 2 sentences under 200 characters, follows the voice;
  only given facts, never invented prices, stock or links; options not in a listed set get the available ones; never
  claims an action (no "we DMed you"), may invite a DM only when the facts say so; confidence 0.8+ only when the facts
  fully answer or no facts are needed (praise, thanks); missing fact gives a holding reply with confidence at most
  0.4 and the fact named in `reason`; spam gets intent `spam` and an empty reply. `REPLY_SCHEMA` (intent, language,
  reply_text, confidence, reason). `reply_request(context)`: compact JSON with empty fields dropped and the caption
  cut to `CAPTION_MAX_LENGTH` (2200, Instagram's limit). The caption rule: its prices, sizes, offers and dates are
  facts, post facts win on a conflict. Learner prompts (constants): `LEARN_RULES` (buyer facts only: price, sizes,
  colors, material, stock, availability (also what is not for sale), offer, delivery, shipping, COD, returns, how to
  order, contact, location, hours; only what the text states; 1 to 3 word labels reused per topic, another item seen
  in the post is labelled with its name (so it never replaces the product's facts); text under 200 characters in the
  text's language; scope `post` for the product, `account` for the shop), `LEARN_CAPTION_SYSTEM` (at most 12 facts),
  `LEARN_REPLY_SYSTEM` (at most 3; a direct answer to the buyer's question is always a fact even off the topic list;
  holding replies, thanks and "DM us" state none), `LEARN_SCHEMA` (`facts[]` of scope, label, text),
  `learn_reply_request(question, answer, post_caption)`.
- `claude.py`: `ClaudeReplyEngine` (`name = "claude"`): one `core.claude.generate_json` call (structured JSON output,
  low effort), max 2000 output tokens (a ceiling with room for the short thinking before the answer). Confidence is
  clamped to 0..1 (unreadable is 0). `ClaudeBrainLearner` (`name = "claude"`): one `generate_json` call, max 4000
  output tokens; facts without a label or text are dropped, an unknown scope becomes `post`, labels are
  cut to 80 and texts to 300 characters.
- `registry.py`: `get_engine()`, `get_learner()`.

**Another engine:** add a module with a `ReplyEngine` / `BrainLearner`, point `HOOKD_REPLY_ENGINE` /
`HOOKD_BRAIN_LEARNER` at it. Routing and sending need no change. Schemas follow Claude's structured output rules
(`additionalProperties: false` on every object, no array length limits).

## Models

| Model | Fields |
|---|---|
| `AgentProfile` (one per `SocialAccount`, `related_name="agent_profile"`, created lazily) | `enabled` (False), `voice`, `knowledge` (account brain, one fact per line), `reply_to_threads` (False), `paused_until`, `pause_reason`, `next_send_at` |
| `PostBrain` (one per `content.Post`, `related_name="brain"`) | `knowledge` (video brain), `agent_enabled` (True), `caption_learned_at` (set once when the caption is read) |
| `LearnedFact` (`related_name="learned_facts"` on `SocialAccount` and `Post`) | `account`, `post` (empty = shop wide), `label`, `text`, `source` (`FactSource`: `caption`, `reply`), `source_post` (post whose caption or comment it came from, SET_NULL), `source_reply` (the inbox reply that taught it, `related_name="learned_facts"` on `CommentReply`, SET_NULL). Property `line` (`Label: text`). Newest first |
| `CommentReply` (one per `comments.Comment`, `related_name="agent_reply"`) | `status` (`pending_review`, `queued`, `sending`, `sent`, `skipped`, `rejected`, `failed`), `draft_text` (engine output), `final_text` (what is sent; property `written_by_creator` when it differs from the draft), `decision` JSON (`intent`, `language`, `confidence`, `engine`, `engine_reason`, `reason`), `scheduled_for`, `sent_at`, `reply_external_id`, `error`, `attempts` |

Choices in `constants.py`: `ReplyStatus`, `FactSource`, `FactScope` (`post`, `account`). All in the Django admin.
A video brain only exists for posts Hookd has synced (`Post` rows); comments on other posts use the account brain.

## Services (`services.py`)

| Function | Does |
|---|---|
| `agent_accounts`, `get_profile`, `update_profile`, `is_paused`, `sending_limits`, `supports_replies` | Settings page |
| `sent_reply_count(account, days=None)` | Replies sent on the account: all time (studio profile) or the last `days` (agent page status bar, 7) |
| `get_post_brain`, `save_post_brain`, `preview_posts` | Video brain, preview post choices (20 newest) |
| `handle_new_comment(comment_id)` | Steps 2 to 4. Returns the outcome |
| `preview_reply(account, text, post, voice=None, knowledge=None)` | Same context and routing (no repeated text check), stores and sends nothing. `voice` and `knowledge` replace the saved ones (the agent page tests unsaved edits). Returns `Preview(decision, outcome, reason)` |
| `schedule_reply`, `send_reply`, `send_overdue_replies` | Steps 5 and 6; recovery sends queued replies more than 5 minutes past their slot |
| `inbox`, `inbox_counts`, `get_user_reply` (`ReplyNotFound`), `approve_reply`, `reject_reply`, `retry_reply` | Inbox. Wrong status raises `ReplyNotEditable`, refused text `ReplyTextRejected` |
| `pending_review_count(user)` | Side panel badge (context processor `context_processors.agent_nav`) |
| `learn_from_post(post_id)`, `learn_from_reply(reply_id)` | Learned brain (see above). Return the outcome (`learned N`, `already_learned`, `failed`, `missing`) |
| `account_learned_facts(account)`, `post_learned_facts(post)`, `post_learned_fact_count(account)`, `forget_fact(user, fact_id)` (`LearnedFactNotFound`) | Learned facts for the pages, and Remove |

`model_querysets.py`: profile (`get_profile`, `get_or_create_profile`, `lock_profile`, `update_profile`), brain (`get_post_brain`, `save_post_brain`, `claim_caption_learning`, `release_caption_learning`), learned facts (`learned_facts`, `account_learned_facts`, `post_learned_facts`, `post_learned_fact_count`, `save_learned_facts`, `get_learned_fact_for_user`, `delete_learned_fact`), replies (`reply_exists`, `create_reply_if_new`, `get_reply`,
`get_reply_for_user`, `update_reply`, `claim_reply_for_sending`, `sent_times_since`,
`outgoing_texts_since`, `list_replies_for_user`, `reply_counts_for_user`,
`pending_review_count`, `sent_reply_count`, `overdue_queued_reply_ids`). Uses `content.model_querysets.find_post`/`latest_posts` and
`comments.model_querysets.get_comment`, `content.model_querysets.get_post`.

## Tasks and command

`tasks.py`: `handle_comment(comment_id)`, `send_reply(reply_id)`, `learn_from_post(post_id)`, `learn_from_reply(reply_id)`;
all return the outcome string. `receivers.py` (connected in `AgentConfig.ready`): `learn_from_published_caption` on `post_published`.
`send_due_replies` management command: cron every 10 minutes (`DEV-infrastructure.md`).

## Pages and JSON actions (`views.py`, `api.py`, `urls.py`, `forms.py`, `templates/agent/`)

Pages are GET only. Every action is a `<form data-api>` that posts with `fetch` to `api.py` (`DEV-web.md`): invalid
forms and `HookdError` answer 400 `{"error"}` (shown as a message), other users' objects 404, logged out 401.

| Route | Name | Page |
|---|---|---|
| `GET /agent/` | `agent` | First account's agent page, or connections |
| `GET /agent/accounts/<id>/` | `agent-account` | Built around what the creator checks and does most. Top: the status bar (`status_bar.html`, context from `views.status_context`): Hooky on or off for the account, what that means, replies sent in the last 7 days, the pause warning, a "N replies waiting for you" link to the inbox when any wait, and the on or off switch (`AgentEnabledForm`, `data-autosubmit`, saved at once by `api-agent-enabled`). Left: `AgentSettingsForm` (initial from the profile, saved by `api-agent-settings`) as three white cards: "What Hooky knows" (account brain, first because it decides what goes out on its own), "How Hooky sounds" (voice, with `VOICE_PICKS` chips that append a style), "Which comments" (thread replies switch). Under the form (`.agent-column`), a separate "What Hooky learned" card lists the learned shop facts (`learned_facts.html`) and how many post facts exist. A save bar (`data-unsaved`) appears after any edit and stays at the bottom of the screen with Discard and Save changes. Right (`#try`, sticky): "Try it", a `PreviewForm` posting to `api-agent-preview` with `data-include="agent-settings"`, so the test uses the unsaved voice and brain; the answer fills `#preview-result`. Bottom: the sending limits as one line with a "How it works" `<details>`. Under 860px the test drops below the settings |
| `GET /agent/inbox/?status=` | `agent-inbox` | Tabs: Needs review, Scheduled, Sent, Skipped, Failed, Rejected (100 newest each, `views.inbox_tabs`). Review items have an editable reply, "Send reply" (approve) and "Discard"; failed ones "Try again" |
| `POST /api/agent/accounts/<id>/settings/` | `api-agent-settings` | `update_profile(**cleaned_data)`, `{message}` |
| `POST /api/agent/accounts/<id>/enabled/` | `api-agent-enabled` | `AgentEnabledForm`: turns the agent on or off at once, `{message, fragments}` redrawing `#agent-status` |
| `POST /api/agent/accounts/<id>/preview/` | `api-agent-preview` | `PreviewForm` (comment, optional post, optional unsaved `voice` and `knowledge`, used only when sent), `preview_reply`, `{html}` from `preview_result.html`. Engine errors are a 400 with the reason |
| `POST /api/agent/posts/<id>/brain/` | `api-agent-post-brain` | `PostBrainForm` from the post page's "Video brain" card, `{message}` |
| `POST /api/agent/facts/<id>/forget/` | `api-agent-fact-forget` | Deletes a learned fact, `{html: "", message}` removes its row (`data-replace=".learned-fact"`) |
| `POST /api/agent/replies/<id>/approve/`, `/reject/`, `/retry/` | `api-reply-approve`, `-reject`, `-retry` | `{html: "", fragments, message}`: the card is removed, `fragments` redraws `#inbox-tabs` (counts), `#nav-replies-count` (side panel badge) and, when the tab is now empty, `#inbox-list` |

Templates: `account.html`, `status_bar.html`, `learned_facts.html` (facts list with Remove, also used by the content post page and modal), `preview_result.html`, `inbox.html`, `inbox_tabs.html`, `inbox_list.html`;
the side panel badge is `components/replies_badge.html`. Look: each inbox item reads like an Instagram thread: the post it is on (thumbnail and caption, from `services.inbox` setting `reply.post`, open the post and its Video brain in the shared modal; Ctrl or Cmd click opens the post page), the comment, then your reply indented under it. A review item adds a butter "Hooky was not sure, so it is asking you" note with the reason , a line saying Hooky learns from the reply the creator writes, and a "teach Hooky" link to the agent page brain, then the draft in a clearly editable white box (`.reply-composer`: pencil label, border, Discard and "Send reply", Ctrl or Cmd + Enter sends via `data-send-shortcut`). Other tabs show the reply with when it was or will be sent ("Written by you, sent" instead of "Sent by Hooky" for `written_by_creator`), what the creator's reply taught ("Hooky learned from your reply": each fact with "this post only" or "all posts", from `source_reply`, prefetched by `list_replies_for_user`; it replaces the certainty line for replies the creator wrote), and Hooky's certainty in words (sure, fairly sure, not sure) instead of the raw confidence. Empty status tabs are dimmed; an emptied review tab shows Hooky with "All caught up" (`inbox_list.html` takes `status`).

## Tests (`tests.py`)

Webhook comment queued once, own comment not queued; unknown post fetched for the agent, stored even with the agent off, fetch failure decides without it; agent off stores nothing; thread, old and post-off comments
skipped; spam skipped; video brain answer sent on its own (Graph call checked); praise thanked on its own; missing
information goes to review; the same person can get several replies; repeated text; refused text; spacing
45 to 75 s; hourly cap reschedules without a call; rate limit pauses and requeues; expired token; never sent twice;
early task waits; `send_due_replies`; Instagram text rules; inbox list, approve edited text (JSON with redrawn tabs,
empty list and badge), approve refused text, reject, retry, side panel count; settings save and on/off checkbox, home redirect, video brain save, preview stores
nothing, 404 for other users. Claude engine (model call mocked): decision mapped and clamped, compact request without
empty fields or rules, unreadable confidence is 0, constant system prompt without `rule_id` in the schema, engine
failure goes to review with the reason, preview shows the error. Learned brain: a synced post is not read until its first comment and then only
once (a later caption change is ignored), read with the agent off, a post fetched for a comment is read, a post
published from Hookd is read, learner failure is retried on the next comment, learned facts answer comments while typed facts
win on the same label, a rewritten inbox reply teaches the next answer, an unchanged approval learns nothing, the same
topic replaces the older fact, pages list facts and Remove works (404 for other users). Claude learner: facts mapped,
cleaned and invalid ones dropped, constant system prompt.
