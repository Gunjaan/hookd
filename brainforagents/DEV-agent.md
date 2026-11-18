# DEV: Reply agent (`backend/apps/agent`)

Hooky, the reply agent, handles every new comment on a connected account and every buyer DM with an **agent loop**:
Claude Haiku decides what to do and does it with tools (look facts up, reply publicly, DM, ask the seller, learn, open a
support ticket, come back later). There are no rules to set up. Hooky acts on its own and asks the seller instead of
guessing; code, not the model, enforces the safety checks and Meta's limits. The model is pluggable: **Claude Haiku**
(`ClaudeAgentModel`), with the word matching **simulated model** standing in when `ANTHROPIC_API_KEY` is empty and in
tests. Brains are filled by the creator on the agent page and the post page, by the post studio's "Teach your reply
agent" questions (`DEV-creatives.md`), and **on their own**: captions, inbox replies the creator rewrote, and facts the
agent saves (see "Learned brain"). Sending is slow and capped on purpose so Hookd stays inside Meta's rate limits and
anti-spam rules.

## Flow

1. **A comment** stored by `comments.services.record_incoming_comments` queues `tasks.handle_comment(comment_id)` on
   commit (never for the account's own comments; studio history never queues the agent). `services.handle_new_comment`
   calls `content.services.ensure_post` (fetches posts made outside Hookd, even with the agent off) and
   `learn_from_post` (reads the caption once, synchronously, also with the agent off). It then ignores the comment (no
   row) when the agent is off, a reply or run already exists, or the platform has no `public_reply` capability; stores
   a `skipped` reply when the video brain turns the agent off, it is a thread reply and `reply_to_threads` is off, or it
   is older than `MAX_COMMENT_AGE_HOURS`, or the person already started `MAX_RUNS_PER_BUYER_PER_DAY` comment runs in
   the last day (stops bot ping-pong). Otherwise it creates an `AgentRun` (status `queued`) with a `comment` event
   and queues `tasks.advance_run` on commit.
2. **A buyer DM** (any new message a person wrote, story replies and mentions included) makes `messaging` send
   `customer_message_received`; `receivers.answer_buyer_message` queues `tasks.handle_buyer_message(conversation_id,
   message_id)`. `services.handle_buyer_message` (agent on, provider has `direct_message`) picks the buyer's run: the
   newest run linked to that conversation, or the newest run whose private reply this person is answering (then links
   the conversation). A recent run (touched within `RUN_CONTINUES_FOR`, 7 days) is continued unless it failed (then a
   new run starts) or was handed to the seller (then the DM is left to the seller: `handed_off`). Otherwise a new run
   without a comment starts (`buyer_external_id`, `buyer_username` from their latest comment when known). Pending
   follow-ups are dropped ("the buyer wrote back", shown in the trail), a `dm` event is added and `advance_run` queued.
   The event waits `DM_BATCH_SECONDS` (10) and every new DM pushes the run's waiting DMs to its own time
   (`delay_pending_dms`), so messages sent together (a shared post and the text typed with it arrive as separate
   webhooks) become one turn; the earlier queued `advance_run` finds nothing due and does nothing.
   A DM that shares a post first calls `content_services.ensure_post` for it, so the seller's own post posted outside
   Hookd is stored (someone else's post fails the fetch and is not stored).
3. **`loop.advance(run_id)`** claims the run (`queries.claim_run`: conditional update to `running`; a second worker
   gets `busy` and leaves its event to the holder; a run left `running` for `STALE_RUN_MINUTES` because its worker died
   is taken over). Due events (unprocessed, `available_at` empty or past) become one
   user turn: a `comment` event is the start text (account, voice, post id, `Commenter: @x`, `Comment: ...`, now); a
   `dm` event is "New DM from the buyer: ..." (a DM started run gets a start text first) plus whether a DM is allowed
   now (`services.dm_route`; a DM started run gets one intro first); a `followup` event is "Follow-up due. Your note:
   ..." plus the DM status and the follow-up rules (one short DM or nothing, no new follow-up). A due follow-up that
   can no longer DM is dropped without calling the model (`drop_followup` action). Events with nothing for the agent
   (an answer to a closed question) are marked done. Then up to `MAX_STEPS` model calls (`_turns`): every `tool_use` block runs through `tools.execute`, each call is stored as an
   `AgentAction`, all results go back in one user turn (`is_error` on refusals). It stops when the model answers
   without tools (its last text is `run.outcome`), or when `ask_seller` was called. Events that arrived meanwhile are
   picked up in the same claim.
4. **Waiting for the seller**: the transcript ends on the assistant turn with the unanswered `ask_seller` call; the
   other results of that turn are kept in `held_results`. The run (`waiting_seller`) only moves on when a
   `seller_answer` event comes: the held results plus `{"tool_result": "The seller answered: ..."}` (or "chose not to
   answer, wrap up" when dismissed, or "did not answer in time, do not guess" when expired) form the next user turn.
   Other events wait until then. A question left open for `QUESTION_EXPIRES_HOURS` (24) expires (`expire_questions`,
   run by `wake_agent_runs`), so no run waits forever.
5. **Settling** (`_settle`): `waiting_seller`, else `waiting_followup` with `wake_at` when a follow-up event is pending,
   else `done`. A comment run that did not reply publicly gets a `skipped` CommentReply with the outcome as reason
   (`note_run_finished`), so the inbox stays complete. Due events found after release queue another `advance_run`.
6. **Ending for good** (`_finish`): `RunTooLong` (the run reached `MAX_RUN_STEPS` in its life) hands the buyer to the
   seller (`handed_off`); a `HookdError` (Claude down, refusal, cut off answer, `StepLimitReached` = `MAX_STEPS` calls
   in one turn) or any other exception (a bug, logged) marks it `failed`. Either way `wake_at` is cleared, pending
   events are dropped, and a comment gets (or its skipped row becomes) a `pending_review` reply with an empty draft and
   the reason, so the creator answers by hand (`note_run_failed`). These runs never wake again; nothing is ever left
   `running`.
7. **Sending**: public replies (`CommentReply`) and DMs (`OutgoingDM`) share one clock per account: `_take_slot` locks
   the `AgentProfile`, takes `max(now, next_send_at, paused_until, not_before)` and moves `next_send_at` by
   `MIN_SPACING_SECONDS` plus 0 to `JITTER_SECONDS`. `schedule_reply` / `schedule_dm` mark them `queued` and queue
   `tasks.send_reply` / `tasks.send_dm` with `eta=slot`. `send_reply` / `send_dm` return early when not queued or more
   than 5 s early (`not_due`), claim the row (`queued -> sending`, only one caller wins), then: account not active ->
   `failed`; paused -> rescheduled after the pause; hourly (`MAX_PER_HOUR`) or daily (`MAX_PER_DAY`) cap over both
   outboxes -> rescheduled; reply text problems -> `pending_review`; a DM Meta no longer allows (private reply past
   7 days + 1 h, DM outside the 24 h window) -> `failed`. `RateLimitedError` pauses the account for
   `RATE_LIMIT_PAUSE_MINUTES` and requeues; `TokenExpiredError` marks the account `expired`; other `ProviderAPIError`
   -> `failed`. A sent private reply links the run to the conversation of the returned `recipient_id` (the
   commenter's id), so their answer continues the run.

## Tools (`tools.py`, schemas in `engines/prompts.TOOLS`)

Each returns a `ToolResult(text, is_error, pauses)`; the text is the `tool_result` the model reads. Errors are how the
code says no, so the model corrects itself.

| Tool | Does | Refused when |
|---|---|---|
| `search_brain(topic)` | The whole brain: shop facts, then this post's facts (`brain_parts`) | |
| `get_post(post_id)` | Caption (2200 cut), type, link and video brain; empty id = the run's post: the commented post, or in a DM run the post the buyer shared most recently in the conversation (`loop._context`, which also makes it the post `search_brain` and `save_fact` use) | not a stored post (someone else's shared post: the model is told to use the caption the DM gave) |
| `reply_to_comment(text)` | Adds `@author` when missing, stores the `CommentReply` (`agent_reply`: draft = final = text, `decision.approval`), schedules it, or holds it in approval mode | DM started run (no comment), platform without public replies, already replied (a `skipped` row is reused), `reply_text_problems`, number guard, the same text `MAX_SAME_TEXT_PER_HOUR` times this hour |
| `send_dm(text)` | `dm_route`: a normal `dm` while the buyer's 24 h window is open (5 min margin), else the comment's one `private_reply` while the comment is under `PRIVATE_REPLY_MAX_HOURS` (167 h) old. Taking the private reply sets `private_reply_used_at` at once. Stores an `OutgoingDM`, schedules it or holds it in approval mode | not allowed now (the reason is in the error), capability missing, `dm_text_problems`, number guard |
| `ask_seller(question)` | `SellerQuestion` (open, with the `tool_use_id`); the run pauses | empty, a second question in the same turn, `MAX_QUESTIONS` (3) already asked in this run |
| `save_fact(label, fact, scope)` | One learned fact (scope `post` needs a post, else shop wide) | empty label or fact |
| `check_tickets()` | This buyer's tickets on the account (`buyer_external_id`): number, date, product, summary, status, seller's note | |
| `create_ticket(product, summary)` | `SupportTicket` (open) and `tasks.email_ticket` on commit; result has the ticket number | empty summary, empty product ("ask the buyer first"), the buyer already has an unresolved ticket for that product (case insensitive: "share its status instead") |
| `schedule_followup(hours, note)` | A `followup` event with `available_at`, `wake_at`, `followups_used + 1`, `tasks.advance_run` with `eta` | the turn is itself a follow-up (no chasing twice), hours outside 1 to `FOLLOWUP_MAX_HOURS`, `MAX_FOLLOWUPS` used, one already pending, due after `dm_allowed_until` (open window end, or comment + 167 h while the private reply is unused), or no DM allowed at all |

`tools.execute` checks the arguments against the tool's signature ("Wrong arguments for x" goes back to the model);
errors inside a tool are not hidden and end the run as failed.

**When a follow-up is dropped**: the buyer writes back before it is due (`cancel_followups`); it comes due when
Instagram no longer allows a DM (dropped in code, no model call); the run is handed off or failed (pending events
dropped). When it does come due the model is told to send one short DM or nothing, and it cannot schedule another.

**Number guard** (`unbacked_numbers`): numbers in a reply or DM (`Rs 1,299` and `1299.00` both read `1299`; @handles
ignored) must appear in what the agent was told in this run: user turns (comment, DMs, follow-up notes), tool results
and seller answers, including results earlier in the same turn. Otherwise: "1299 is not in the brain, use search_brain
or ask_seller."

**Dry run** (`ToolContext.dry_run`, the agent page's "Try it"): nothing is stored or sent, but tools answer as if they
had acted ("Reply scheduled...", "Ticket #0 opened...", `check_tickets` sees the dry run's own tickets), so the model
behaves as it would for real.

## Learned brain

The agent builds its own memory from what goes through Hookd, stored as `LearnedFact` rows next to (never inside)
what the creator typed.

| Source | Trigger | What is learned |
|---|---|---|
| Post caption, first comment | `handle_new_comment` calls `learn_from_post` for the comment's post (stored, or just fetched by `ensure_post`) | `learn_from_post`: claims the post (`claim_caption_learning`: a conditional update that sets `PostBrain.caption_learned_at` only while it is empty, so only one caller ever wins), then `learner.from_caption(caption)`. Once claimed it never reads that post again (`already_learned`), even if the caption changes. An empty caption learns nothing but stays claimed. A learner error releases the claim (`release_caption_learning`), logs and returns `failed`, so the next comment retries |
| Post caption, published from Hookd | `receivers.learn_from_published_caption` on `content.signals.post_published`: queues `tasks.learn_from_post` on commit | Same as above |
| Inbox reply | `approve_reply` when `CommentReply.written_by_creator` (the sent text differs from the draft, leading @mention ignored): queues `tasks.learn_from_reply` on commit | `learn_from_reply`: `learner.from_reply(question, answer, post_caption)`, the facts the creator's answer states, linked to the reply (`source_reply`) |
| The agent | `save_fact(label, fact, scope)`, usually right after the seller answered `ask_seller` | One fact, `FactSource.AGENT`, linked to the run (`source_run`) |

Each fact has a scope: `post` facts go on the post (`LearnedFact.post`), `account` facts (shipping, returns, how to
order) are shop wide (`post` empty); a fact without a stored post is shop wide. Saving replaces older learned facts on
the same topic: same place (that post, or shop wide) and same label, case insensitive (`save_learned_facts`).

Using them: `brain_parts` (the `search_brain` tool) loads up to `LEARNED_FACTS_LIMIT` (40) newest shop facts and post
facts and appends them as `Label: text` lines after the creator's brain (`_brain_text`). A learned fact whose label
matches a creator line (`Label: ...`) is left out, so what the creator typed always wins. The whole brain comes back on
every call (the topic is only for the trail), so "kitne ka hai" or "cost" never misses a "Price" line. The caption is
read with `get_post`.

UI: the agent page's "What Hooky learned" card lists shop facts and counts post facts; the post page and post modal list
the post's facts under the Video brain. Each fact has Remove (`api-agent-fact-forget`).

## Meta compliance (why the limits exist)

- Replies only to new comments people wrote on the creator's own posts, never to the account's own comments, and no
  thread replies unless the creator turns them on. One public reply per comment.
- DMs (checked in Meta's private replies docs): one private reply per comment, within 7 days of it; more messages only
  after the person writes, within 24 hours of their message. No cold DMs. Needs `instagram_manage_messages`.
- Replies and DMs go out one at a time per account, 45 to 75 s apart, max 20 an hour and 150 a day together
  (`AGENT_REPLY_LIMITS`). Repeated identical text is refused because Instagram flags copy-paste replies as spam.
- Graph rate limit and policy block errors (codes 4, 17, 32, 613, 368, HTTP 429) pause the account for an hour.
- Instagram text rules are checked before sending (`DEV-instagram.md`).
- The agent starts **off** per account. **Approval mode** (`approve_before_sending`) holds every reply and DM in the
  inbox for the creator to send; the run goes on ("Waiting for the seller's approval").

## Settings (`config/settings.py`)

| Setting | Default | Notes |
|---|---|---|
| `HOOKD_AGENT_MODEL` | `apps.agent.engines.claude.ClaudeAgentModel` when `ANTHROPIC_API_KEY` is set and not testing, else `apps.agent.engines.simulated.SimulatedAgentModel` | Dotted path to an `AgentModel` |
| `AGENT_REPLY_MODEL` | env `CLAUDE_REPLY_MODEL`, else `claude-haiku-5-5` | Model for `ClaudeAgentModel` and `ClaudeBrainLearner` |
| `HOOKD_BRAIN_LEARNER` | `...claude.ClaudeBrainLearner` with a key and not testing, else `...simulated.SimulatedBrainLearner` | Dotted path to a `BrainLearner` |
| `AGENT_REPLY_LIMITS` | `MIN_SPACING_SECONDS` 45, `JITTER_SECONDS` 30, `MAX_PER_HOUR` 20, `MAX_PER_DAY` 150, `MAX_SAME_TEXT_PER_HOUR` 3, `MAX_COMMENT_AGE_HOURS` 24, `RATE_LIMIT_PAUSE_MINUTES` 60 | Sending, read by `services.reply_limit` |
| `AGENT_LOOP_LIMITS` | `DM_BATCH_SECONDS` 10 (0 in tests, eager Celery ignores the delay), `MAX_STEPS` 8, `MAX_RUN_STEPS` 40, `MAX_QUESTIONS` 3, `QUESTION_EXPIRES_HOURS` 24, `MAX_FOLLOWUPS` 2, `FOLLOWUP_MAX_HOURS` 167, `PRIVATE_REPLY_MAX_HOURS` 167, `MAX_RUNS_PER_BUYER_PER_DAY` 10, `STALE_RUN_MINUTES` 10, `MAX_OUTPUT_TOKENS` 4000 | How long a buyer DM waits for the next one, model calls per turn and per run, seller questions and their expiry, follow-ups, private reply window (7 days minus 1 h), comment runs per person a day, dead worker takeover, output ceiling per call |

## Engines (`engines/`)

- `base.py`: `ModelStep(content, stop_reason)` and the `AgentModel` ABC (`name`, `step(system, tools, messages)`,
  content blocks in the Claude Messages format). `LearnedFactDraft(scope, label, text)` and the `BrainLearner` ABC
  (`name`, `from_caption`, `from_reply`).
- `claude.py`: `ClaudeAgentModel` calls `ai.claude.run_step` (tools, `tool_choice` auto, effort low, top level
  `cache_control`, `MAX_OUTPUT_TOKENS`); the assistant content comes back as plain dicts, thinking blocks included, and is
  sent back unchanged. `ClaudeBrainLearner`: one `generate_json` call, max 4000 output tokens; facts without a label or
  text are dropped, an unknown scope becomes `post`, labels cut to 80 and texts to 300 characters.
- `prompts.py`: `AGENT_SYSTEM` (constant): who Hooky is; look facts up first, only facts from tools, numbers without a
  tool are refused; missing fact -> `ask_seller`, then `save_fact`; praise -> public thanks, interest -> DM or a
  follow-up only when it helps the sale; problems -> `check_tickets` first, share status and note, ask which product
  before `create_ticket`, tell the ticket number; spam -> nothing; buyer's language and script, public replies start
  with @username under 200 characters, DMs under 500 without @mention, the voice, no hashtags, at most 1 emoji; never
  claim an action not taken; end with one sentence for the seller. `TOOLS` (constant order, `strict`, every property
  required, `additionalProperties: false`). Learner prompts (constants): `LEARN_RULES`, `LEARN_CAPTION_SYSTEM` (at most
  12 facts), `LEARN_REPLY_SYSTEM` (at most 3), `LEARN_SCHEMA`, `learn_reply_request`. `CAPTION_MAX_LENGTH` 2200.
- `simulated.py`: `SimulatedAgentModel`: `search_brain` on the comment, then `reply_to_comment` with the brain line
  sharing the most words (stop words dropped, plural "s" ignored, heading lines skipped); none and a "?" ->
  `ask_seller`, and the seller's answer becomes the reply; otherwise "Thank you so much!". Never DMs, saves, tickets or
  follows up. `SimulatedBrainLearner`: caption sentences with a digit become post facts labelled `Caption` (at most 8);
  a rewritten reply becomes one post fact labelled with the question.
- `registry.py`: `get_model()`, `get_learner()`.

**Another model:** add an `AgentModel`, point `HOOKD_AGENT_MODEL` at it. Tools, routing and sending need no change.

## Models

| Model | Fields |
|---|---|
| `AgentProfile` (one per `SocialAccount`, `related_name="agent_profile"`, lazy) | `enabled` (False), `voice`, `knowledge` (account brain), `reply_to_threads` (False), `approve_before_sending` (False), `paused_until`, `pause_reason`, `next_send_at` |
| `PostBrain` (one per `content.Post`, `related_name="brain"`) | `knowledge` (video brain), `agent_enabled` (True), `caption_learned_at` |
| `LearnedFact` | `account`, `post` (empty = shop wide), `label`, `text`, `source` (`caption`, `reply`, `agent`), `source_post`, `source_reply` (`related_name="learned_facts"`), `source_run` (`related_name="learned_facts"`), all SET_NULL. Property `line`. Newest first |
| `CommentReply` (one per `comments.Comment`, `related_name="agent_reply"`) | `status` (`ReplyStatus`), `draft_text`, `final_text` (property `written_by_creator`), `decision` JSON (`engine`, `reason`, `approval`), `scheduled_for`, `sent_at`, `reply_external_id`, `error`, `attempts` |
| `AgentRun` | `account`, `comment` (OneToOne, `related_name="agent_run"`, empty for DM started runs), `conversation` (`messaging.Conversation`, SET_NULL), `buyer_external_id`, `buyer_username`, `status` (`RunStatus`: `queued`, `running`, `waiting_seller`, `waiting_followup`, `done`, `handed_off`, `failed`), `messages` (transcript), `held_results`, `steps`, `followups_used`, `private_reply_used_at`, `wake_at`, `outcome`, `error`. Newest first |
| `AgentEvent` (`related_name="events"`) | `run`, `kind` (`EventKind`: `comment`, `seller_answer`, `dm`, `followup`), `payload` (`message_id`, `question_id` or `note`), `available_at`, `processed_at` |
| `AgentAction` (`related_name="actions"`) | `run`, `tool`, `input`, `result`, `is_error`. Oldest first: the "What Hooky did" trail |
| `SellerQuestion` (`related_name="questions"`) | `run`, `tool_use_id`, `question`, `answer`, `status` (`QuestionStatus`: `open`, `answered`, `dismissed`, `expired`), `answered_at` |
| `OutgoingDM` (`related_name="dms"`) | `run`, `kind` (`DMKind`: `private_reply`, `dm`), `text`, `status` (`ReplyStatus`), `scheduled_for`, `sent_at`, `message_external_id`, `error`, `attempts` |
| `SupportTicket` (`related_name="support_tickets"` on `SocialAccount`, `tickets` on `AgentRun`) | `account`, `run` (SET_NULL), `buyer_external_id`, `buyer_username`, `product`, `summary`, `status` (`TicketStatus`: `open`, `in_progress`, `resolved`), `seller_note` (Hooky may share it), `emailed_at` |

Choices in `constants.py`. All in the Django admin (runs with their events and actions inline).

## Services (`services.py`) and the loop (`loop.py`)

Every model and learner call runs inside `ai.services.attributed`: `comment_agent` (the run's account, subject the
comment or, for a DM run, the run), `reply_preview`, `learn_caption`, `learn_reply` (`DEV-ai.md`).

| Function | Does |
|---|---|
| `handle_new_comment(comment_id)`, `handle_buyer_message(conversation_id, message_id)` | Flow steps 1 and 2. Return the outcome (`started`, `queued`, `skipped`, `ignored`, `exists`, `missing`) |
| `advance_run(run_id)` -> `loop.advance`, `advance_run_later(run)`, `wake_overdue_runs()`, `expire_questions()`, `cancel_followups(run, reason)` | Run the loop; recovery wakes runs (not finished ones) whose `wake_at` is more than 5 minutes past; questions expire; follow-ups dropped |
| `brain_parts`, `post_brain_text`, `same_text_count`, `dm_route`, `dm_allowed_until` | Used by the tools |
| `agent_reply`, `agent_dm`, `schedule_followup`, `create_ticket`, `buyer_tickets`, `email_ticket(ticket_id)` | What the tools do. The ticket email goes to `SUPPORT_EMAIL` (account, buyer, product, problem, the comment) |
| `note_run_finished`, `note_run_failed` | Inbox rows for comment runs (flow steps 5 and 6) |
| `preview_run(account, text, post, voice, knowledge)` | `loop.preview`: a dry run on an unsaved comment by `customer`; returns `Preview(trail, outcome, asked_seller)` |
| `schedule_reply`, `schedule_dm`, `send_reply`, `send_dm`, `send_overdue_replies`, `send_overdue_dms` | Sending (flow step 7) |
| `inbox(user, status)` (each reply with `post` and `run`), `inbox_counts`, `open_questions`, `open_question_count`, `pending_dms`, `pending_dm_count`, `attention_count` (pending replies + pending DMs + open questions: sidebar badge, status bar) | Inbox |
| `approve_reply`, `reject_reply`, `retry_reply`, `approve_dm`, `reject_dm` (a discarded private reply frees it), `answer_question`, `dismiss_question` (add a `seller_answer` event and queue the run) | Inbox actions. Wrong status raises `ReplyNotEditable` / `QuestionNotOpen`, refused text `ReplyTextRejected`, unknown ids `ReplyNotFound` / `QuestionNotFound` / `TicketNotFound` |
| `tickets(user, status)`, `ticket_counts`, `get_user_ticket`, `update_ticket(ticket, status, seller_note)` | Tickets page |
| `learn_from_post`, `learn_from_reply`, `account_learned_facts`, `post_learned_facts`, `post_learned_fact_count`, `forget_fact`, `buyer_message_text` | Learned brain; how a DM reads to the agent (a share reads `(shared a post, post_id X, caption: "...")`, caption cut at `SHARED_CAPTION_PREVIEW` 300, then the buyer's text) |

`model_querysets.py` holds every query: profile, brains, learned facts, replies (`sent_times_since` counts replies and
DMs), runs (`create_run_if_new`, `create_dm_run`, `claim_run`, `update_run`, `latest_run_for_conversation`,
`latest_run_for_participant`, `due_wake_run_ids`, `runs_for_comments`), events (`add_event`, `due_events`,
`mark_events_processed`, `delay_pending_dms`, `next_event_time`: follow-ups only), actions, questions, DMs (`claim_dm_for_sending`, `overdue_queued_dm_ids`)
and tickets. Uses `content`, `comments` (`get_comment`, `latest_comment_by_author`) and `messaging` (`get_conversation`,
`get_message`, `get_or_create_conversation`) querysets.

## Tasks, receivers and commands

`tasks.py`: `handle_comment`, `handle_buyer_message`, `advance_run`, `send_reply`, `send_dm`, `email_ticket`,
`learn_from_post`, `learn_from_reply`; each calls one service and returns its outcome. `receivers.py`:
`learn_from_published_caption` (`content.signals.post_published`), `answer_buyer_message`
(`messaging.signals.customer_message_received`). Commands (cron every 10 minutes, `DEV-infrastructure.md`):
`send_due_replies` (lost reply tasks), `wake_agent_runs` (expires seller questions, then lost DM tasks and
follow-ups). `email_ticket` retries up to 5 times with backoff on SMTP or network errors.

## Pages and JSON actions (`views.py`, `api.py`, `urls.py`, `forms.py`, `templates/agent/`)

Pages are GET only. Every action is a `<form data-api>` that posts with `fetch` to `api.py` (`DEV-web.md`): invalid
forms and `HookdError` answer 400 `{"error"}`, other users' objects 404, logged out 401.

| Route | Name | Page |
|---|---|---|
| `GET /agent/` | `agent` | First account's agent page, or connections |
| `GET /agent/accounts/<id>/` | `agent-account` | Top: the status bar (`status_bar.html`, `views.status_context`): on or off, what that means (approval mode says everything is checked first), replies sent in 7 days, the pause warning, "N things waiting for you" (`attention_count`), the on/off switch (`api-agent-enabled`). Left: `AgentSettingsForm` as three cards: "What Hooky knows" (account brain), "How Hooky sounds" (voice, `VOICE_PICKS` chips), "What Hooky handles" (thread replies switch, approval mode switch). Then "What Hooky learned" (`learned_facts.html`). A save bar (`data-unsaved`). Right (`#try`, sticky): "Try it", a `PreviewForm` posting to `api-agent-preview` with `data-include="agent-settings"` (unsaved voice and brain); the answer fills `#preview-result` with a badge (Handles it, Asks you first, Does nothing), the outcome, the public reply bubble and the open trail. Bottom: the sending limits and "How it works" |
| `GET /agent/inbox/?status=` | `agent-inbox` | Tabs (`views.inbox_tabs`): **Hooky asks** (open questions: the buyer's comment or DM, the question, an answer box with Answer and Dismiss), Needs review (pending replies and, in approval mode, pending DMs with Send DM and Discard), Scheduled, Sent, Skipped, Failed, Rejected. Without `status` it opens Hooky asks when questions wait. Every card has the collapsible "What Hooky did" trail (`run_trail.html`) and the run's outcome. Header links to Tickets and settings |
| `GET /agent/tickets/?status=` | `agent-tickets` | Tabs Open, In progress, Resolved. Each ticket: number, product, account, buyer, summary and a `TicketForm` (prefix `t<id>`: status, note for the buyer) saved by `api-ticket-update` |
| `POST /api/agent/accounts/<id>/settings/`, `/enabled/`, `/preview/` | `api-agent-settings`, `api-agent-enabled`, `api-agent-preview` | Save settings; switch on or off (`{message, fragments}` redrawing `#agent-status`); dry run (`{html}` from `preview_result.html`, engine errors 400) |
| `POST /api/agent/posts/<id>/brain/`, `/api/agent/facts/<id>/forget/` | `api-agent-post-brain`, `api-agent-fact-forget` | Video brain; remove a learned fact |
| `POST /api/agent/replies/<id>/approve/`, `/reject/`, `/retry/`; `/api/agent/dms/<id>/approve/`, `/reject/`; `/api/agent/questions/<id>/answer/`, `/dismiss/` | `api-reply-*`, `api-dm-approve`, `api-dm-reject`, `api-question-answer`, `api-question-dismiss` | `{html: "", fragments, message}`: the card is removed, `fragments` redraws `#inbox-tabs`, `#nav-replies-count` and, when the tab is now empty, `#inbox-list` (`views.inbox_list_context`) |
| `POST /api/agent/tickets/<id>/` | `api-ticket-update` | `{message}` |

Templates: `account.html`, `status_bar.html`, `learned_facts.html`, `preview_result.html`, `run_trail.html` (actions or
dry run trail items: tool, arguments, result, errors in red), `buyer_said.html` (the comment, or "wrote you a DM"),
`inbox.html`, `inbox_tabs.html` (also the tickets tabs), `inbox_list.html`, `tickets.html`; the side panel badge is
`components/replies_badge.html`. Inbox look: each card reads like an Instagram thread (post, comment, your reply);
review cards explain why (approval mode, or Hooky could not finish) and have an editable `.reply-composer`.

## Tests (`tests.py`)

A `ScriptedModel` plays back model turns (`call`, `calls`, `done`) and keeps every transcript it was sent. Covered:
webhook comment queued once, own comment not; unknown post fetched for `get_post`, stored with the agent off, fetch
failure is a tool error; a shared post in a DM is fetched, shown with its id and caption and read by `get_post`, someone
else's shared post still shows its caption and is not stored; a share and the text sent with it are read in one turn; agent off stores nothing; thread, old and post-off comments skipped without a run. Loop: brain
answer sent (Graph call checked); praise thanked; invented price refused by the number guard and corrected; numbers
from the comment allowed, digits in handles ignored; missing fact -> `ask_seller`, run waits, the inbox answer resumes
it with the exact tool_result, `save_fact` stored with the run, reply queued; held results returned with the answer;
dismissed question; spam left alone shows as skipped; model failure and step limit go to review; one public reply per
comment and platform text rules; repeated text; approval mode holds reply and DM, DM approval schedules it; a discarded
private reply is freed; a busy run leaves new events to the holder. DMs: one private reply per comment, sent with
`recipient.comment_id`, links the conversation; 7 day window; the buyer's answer continues the run with a normal DM
(`recipient.id`); a DM past its window fails at send time; a DM started run asks for the product, opens a ticket
(email, Tickets page, status update) and later reports its status and note; a DM run cannot reply publicly; DMs
ignored with the agent off. Follow-ups: limits, wake by `wake_agent_runs`, private reply on wake; none once DMs are no
longer allowed. Number guard unit test. Sending: spacing, hourly cap, rate limit pause, expired token, never twice,
early task, `send_due_replies`. Inbox, settings (approval switch), preview (trail, stores nothing, unsaved brain), 404
for other users on every action. Claude agent model: two steps recorded as `comment_agent` with the comment, thinking
blocks sent back unchanged, constant system and tools with `cache_control`; refusal fails the run; tools match their
handlers and are strict. Instagram reply and DM text rules. Run limits: a long conversation is handed off and later DMs are left to the seller;
a crash ends the run with nothing pending; a dead worker's run is taken over after 10 minutes; at most 3 seller
questions; an unanswered question expires and the run wraps up; a follow-up is dropped when the buyer writes back, and
without a model call when no DM is allowed; a follow-up cannot chain; the same problem is not ticketed twice; one
person cannot start endless runs; wrong tool arguments are an error but tool bugs are not hidden. Learned brain and
Claude learner as before.
