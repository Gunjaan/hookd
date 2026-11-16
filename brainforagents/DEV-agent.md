# DEV: Reply agent (`backend/apps/agent`)

Answers new comments on connected accounts following the creator's rules, using an account brain and a per post
video brain. The engine that writes replies is pluggable; today it is a **simulated engine** (no AI calls) until
Claude is connected. Sending is slow and capped on purpose so Hookd stays inside Meta's rate limits and anti-spam rules.

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
3. It builds an `AgentContext` (comment, voice, account brain, post caption, video brain, enabled rules: post rules
   first, then account rules, each by `position`) and calls `engines.registry.get_engine().decide(context)`.
4. `_route` turns the decision into a status (first match wins):

   | Condition | Status |
   |---|---|
   | Matched rule says ignore, or no rule and fallback is ignore | `skipped` |
   | `provider.reply_text_problems(text)` is not empty | `pending_review` (reason = the problems) |
   | Matched rule says review, or no rule and fallback is review | `pending_review` |
   | Confidence below `AUTO_REPLY_MIN_CONFIDENCE` | `pending_review` |
   | The commenter already has a queued or sent reply on this post in the last 24 h (`MAX_PER_AUTHOR_PER_POST_PER_DAY`) | `pending_review` |
   | The same text (leading @mention ignored) went out `MAX_SAME_TEXT_PER_HOUR` times in the last hour | `pending_review` |
   | Otherwise (rule says reply automatically) | `queued` via `schedule_reply` |

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

- Replies only to new comments people wrote on the creator's own posts, never to the account's own comments, at most
  one automatic reply per person per post per day, and no thread replies unless the creator turns them on.
- Replies go out one at a time per account, 45 to 75 s apart, max 20 an hour and 150 a day (`AGENT_REPLY_LIMITS` in
  settings). Repeated identical text is held for review because Instagram flags copy-paste replies as spam.
- Graph rate limit and policy block errors (codes 4, 17, 32, 613, 368, HTTP 429) pause the account for an hour instead
  of retrying.
- Instagram text rules are checked before sending (`DEV-instagram.md`).
- The agent starts **off** per account and starter rules only draft for review, so nothing is posted until the creator
  switches a rule to "Reply on its own".

## Settings (`config/settings.py`, no env vars)

| Setting | Default | Notes |
|---|---|---|
| `HOOKD_REPLY_ENGINE` | `apps.agent.engines.simulated.SimulatedReplyEngine` | Dotted path to a `ReplyEngine` |
| `AGENT_REPLY_LIMITS` | see below | One dict, read by `services._limit` |

`AGENT_REPLY_LIMITS`: `MIN_SPACING_SECONDS` 45, `JITTER_SECONDS` 30, `MAX_PER_HOUR` 20, `MAX_PER_DAY` 150,
`MAX_PER_AUTHOR_PER_POST_PER_DAY` 1, `MAX_SAME_TEXT_PER_HOUR` 3, `MAX_COMMENT_AGE_HOURS` 24,
`AUTO_REPLY_MIN_CONFIDENCE` 0.75, `RATE_LIMIT_PAUSE_MINUTES` 60.

## Engines (`engines/`)

- `base.py`: `RuleSpec(id, name, when, keywords, action, how_to_reply)`, `AgentContext(comment_text, author_username,
  account_username, voice, account_knowledge, post_caption, post_knowledge, rules)`, `AgentDecision(rule_id, intent,
  language, reply_text, confidence, reason)`, `ReplyEngine` ABC (`name`, `decide(context)`). Engines never send.
- `simulated.py`: `SimulatedReplyEngine`. First rule with a keyword in the comment (case insensitive, whole words,
  emojis work). Reply = `@author ` + the brain line (video brain first, then account brain) sharing the most words
  with the comment (stop words dropped, plural "s" ignored), else the rule's `how_to_reply` as is, else
  "Thank you so much!". Confidence: rule + fact 0.9, rule + text 0.8, fact only 0.6, otherwise 0.5. Ignores `when`
  and `voice`; language is always `unknown`.
- `registry.py`: `get_engine()`.

**Plugging in Claude:** add `engines/claude.py` with a `ReplyEngine` that sends the context (rules with `when`,
voice, both brains) and returns an `AgentDecision`, set `HOOKD_REPLY_ENGINE`, add the API key env var to
`.env.example` and `DEV-infrastructure.md`. Routing and sending need no change.

## Models

| Model | Fields |
|---|---|
| `AgentProfile` (one per `SocialAccount`, `related_name="agent_profile"`, created lazily) | `enabled` (False), `fallback_action` (`review`/`ignore`, default `review`), `voice`, `knowledge` (account brain, one fact per line), `reply_to_threads` (False), `paused_until`, `pause_reason`, `next_send_at` |
| `PostBrain` (one per `content.Post`, `related_name="brain"`) | `knowledge` (video brain), `agent_enabled` (True) |
| `ReplyRule` (`account`, optional `post`) | `name`, `when` (plain English, for the real agent), `keywords` (comma list, `keyword_list` property), `action` (`auto_reply`/`review`/`ignore`), `how_to_reply`, `position`, `enabled`. Ordered by `position, pk` |
| `CommentReply` (one per `comments.Comment`, `related_name="agent_reply"`) | `status` (`pending_review`, `queued`, `sending`, `sent`, `skipped`, `rejected`, `failed`), `rule` (SET_NULL), `draft_text` (engine output), `final_text` (what is sent), `decision` JSON (`intent`, `language`, `confidence`, `engine`, `engine_reason`, `reason`), `scheduled_for`, `sent_at`, `reply_external_id`, `error`, `attempts` |

Choices in `constants.py`: `RuleAction` (Reply on its own, Draft a reply for me to approve, Don't reply), `FallbackAction` (the last two), `ReplyStatus`. All in the Django admin.
A video brain only exists for posts Hookd has synced (`Post` rows); comments on other posts use the account brain.

## Services (`services.py`)

| Function | Does |
|---|---|
| `agent_accounts`, `get_profile`, `update_profile`, `is_paused`, `sending_limits`, `supports_replies` | Settings page |
| `get_post_brain`, `save_post_brain`, `preview_posts` | Video brain, preview post choices (20 newest) |
| `list_rules`, `list_post_rules`, `get_user_rule` (`RuleNotFound`), `create_rule`, `update_rule`, `delete_rule` | Rules |
| `add_starter_rules(account)` | Adds the missing `STARTER_RULES` (spam ignore, price, size or stock, praise; all review except spam). Returns how many |
| `handle_new_comment(comment_id)` | Steps 2 to 4. Returns the outcome |
| `preview_reply(account, text, post)` | Same context and routing (no per author or repeated text checks), stores and sends nothing. Returns `Preview(decision, rule, outcome, reason)` |
| `schedule_reply`, `send_reply`, `send_overdue_replies` | Steps 5 and 6; recovery sends queued replies more than 5 minutes past their slot |
| `inbox`, `inbox_counts`, `get_user_reply` (`ReplyNotFound`), `approve_reply`, `reject_reply`, `retry_reply` | Inbox. Wrong status raises `ReplyNotEditable`, refused text `ReplyTextRejected` |
| `pending_review_count(user)` | Sidebar badge (context processor `context_processors.agent_nav`) |

`model_querysets.py`: profile (`get_profile`, `get_or_create_profile`, `lock_profile`, `update_profile`), brain (`get_post_brain`, `save_post_brain`), rules (`rules_for_comment`,
`list_rules_for_account`, `list_rules_for_post`, `rule_names`, `next_rule_position`, `create_rule`, `update_rule`,
`delete_rule`, `get_rule_for_user`), replies (`reply_exists`, `create_reply_if_new`, `get_reply`,
`get_reply_for_user`, `update_reply`, `claim_reply_for_sending`, `sent_times_since`,
`outgoing_to_author_on_post_since`, `outgoing_texts_since`, `list_replies_for_user`, `reply_counts_for_user`,
`pending_review_count`, `overdue_queued_reply_ids`). Uses `content.model_querysets.find_post`/`latest_posts` and
`comments.model_querysets.get_comment`.

## Tasks and command

`tasks.py`: `handle_comment(comment_id)`, `send_reply(reply_id)`; both return the outcome string.
`send_due_replies` management command: cron every 10 minutes (`DEV-infrastructure.md`).

## Pages (`views.py`, `urls.py`, `forms.py`, `templates/agent/`)

| Route | Name | Page |
|---|---|---|
| `GET /agent/` | `agent` | First account's agent page, or connections |
| `GET/POST /agent/accounts/<id>/` | `agent-account` | One `AgentSettingsForm` in four numbered steps: 1 "What should Hookd do with a new comment?" as one `mode` radio: "Draft a reply for me to approve" (`review`), "Only handle my special cases" (`ignore`, offered only when the account has rules, since without rules it equals off) and "Turn off auto replies" (`off`: `enabled` False, old fallback kept). Mapping in `AgentSettingsForm.initial_for` / `profile_fields`; enabled with fallback ignore and no rules shows as off, 2 "Replies sound like you" (voice, account brain as "What should the agent know?", thread replies), 3 "Special cases" (optional, a `<details>` that starts open when rules exist): rules as an IF / ELSE IF chain, post rules first, ending in a read only OTHERWISE row showing the fallback, 4 safe sending limits in a `<details>`. The empty state's "Add ready made ones" button posts the same form to `agent-starter-rules` via `formaction`. Pause warning on top |
| `POST /agent/accounts/<id>/starter-rules/` | `agent-starter-rules` | Adds starter rules |
| `GET/POST /agent/accounts/<id>/preview/` | `agent-preview` | `PreviewForm`: comment and optional post, shows outcome, rule, confidence and reply |
| `GET/POST /agent/accounts/<id>/rules/new/?post=<id>` | `agent-rule-new` | `RuleForm` (needs keywords or `when`) in three steps: If (keywords, description), Then (action, reply), Name. With `post`, the rule is for that post and saving returns to the post |
| `GET/POST /agent/rules/<id>/edit/`, `POST .../delete/` | `agent-rule-edit`, `agent-rule-delete` | |
| `POST /agent/posts/<id>/brain/` | `agent-post-brain` | `PostBrainForm` from the post page's "Video brain" card |
| `GET /agent/inbox/?status=` | `agent-inbox` | Tabs: Needs review, Scheduled, Sent, Skipped, Failed, Rejected (100 newest each). Review items have an editable reply, "Approve and send" and "Discard" |
| `POST /agent/replies/<id>/approve/`, `/reject/`, `/retry/` | `agent-reply-approve`, `-reject`, `-retry` | Back to the inbox with a message |

Templates: `account.html`, `rule_item.html` (one rule as "IF name / a comment mentions [chips] / THEN action / reply", optional `prefix` such as "Else if"; also used on the post page), `rule_form.html`, `preview.html`,
`inbox.html`. Other users' accounts, posts, rules and replies give 404.

## Tests (`tests.py`)

Webhook comment queued once, own comment not queued; unknown post fetched for the agent, stored even with the agent off, fetch failure decides without it; agent off stores nothing; thread, old and post-off comments
skipped; ignore rule; review rule drafts from the video brain; auto rule sends (Graph call checked); post rule beats
account rule; fallback review and ignore; low confidence; per author cap; repeated text; refused text; spacing
45 to 75 s; hourly cap reschedules without a call; rate limit pauses and requeues; expired token; never sent twice;
early task waits; `send_due_replies`; Instagram text rules; inbox list, approve edited text, approve refused text,
reject, retry, sidebar count; settings save, special cases option needs rules, turning off keeps the fallback, home redirect, starter rules once, rule create/edit/delete, rule
validation, video brain save, preview stores nothing, 404 for other users.
