# DEV: Messaging (`backend/apps/messaging`)

Stores direct messages on connected accounts: DMs, replies to the account's stories and story mentions. Messages are
stored per conversation and every conversation knows when the person last wrote, which opens Meta's 24 hour reply
window. Every new message a person writes is handed to the reply agent (`DEV-agent.md`), which answers it and sends its
own DMs; there is no messaging page, conversations are in the Django admin.

## Flow

1. Meta POSTs a `messages` field event to `/webhooks/instagram/` (same endpoint and signature check as comments,
   `DEV-comments.md`). The field must be subscribed in the Meta app and listed in `INSTAGRAM_WEBHOOK_FIELDS`
   (`DEV-instagram.md`).
2. `integrations.services.process_webhook` gets `IncomingMessage` objects from `provider.parse_webhook_messages`.
3. `services.record_incoming_messages(platform, messages)` finds the `SocialAccount` (unknown accounts are logged and
   skipped, still 200), gets or creates the `Conversation` for the other person, and creates each message once
   (unique per conversation and platform id, so Meta's retries are no-ops). A new message from the person (not an
   echo) moves `last_customer_message_at` forward, never back, since deliveries can arrive out of order, and sends
   `signals.customer_message_received(conversation, message)`; the agent's receiver queues
   `agent.tasks.handle_buyer_message` on commit. Echoes (including the agent's own DMs) are stored only.

## Models

| Model | Fields |
|---|---|
| `Conversation` (`related_name="conversations"` on `SocialAccount`) | `account`, `participant_external_id` (the person's platform scoped id; unique with `account`), `last_customer_message_at`. Property `reply_window_ends_at` = last customer message + `REPLY_WINDOW` (24 hours) |
| `DirectMessage` (`related_name="messages"`) | `conversation`, `external_id` (platform `mid`), `is_from_account` (echo of a message the account sent, from any tool), `kind` (`integrations.constants.MessageKind`: `text`, `story_reply`, `story_mention`, `media` (photo or video), `share` (a shared post or reel), `unsupported`), `text`, `story_external_id` (the account's story a story reply answers), `media_url` (story or attachment CDN URL, expires), `reply_to_external_id`, `shared_post_external_id` and `shared_post_caption` (the shared post's id and caption as the platform sent them), `sent_at`, `payload` (raw event) |

Admin: conversations (with the reply window end and the messages inline) and messages, searchable by text and ids.

## Files

`models.py`, `model_querysets.py` (`get_or_create_conversation`, `create_message_if_new`, `note_customer_message`,
`get_conversation`, `get_message`, `latest_shared_post_id`: the post the person shared most recently), `signals.py` (`customer_message_received`), `services.py`, `admin.py`, `tests.py` (DM stored once and opens the window, story reply and mention and media kinds, shared post and reel id and caption,
echo flagged without moving the window, out of order delivery keeps the latest time, deleted messages, read events
and unknown accounts skipped).
