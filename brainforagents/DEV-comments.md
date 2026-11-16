# DEV: Comments (`backend/apps/comments`)

Stores comments on connected accounts' posts as they arrive from platform webhooks and hands new ones to the reply
agent (`DEV-agent.md`).

## Flow

1. Meta POSTs to `/webhooks/instagram/` (view `apps.integrations.views.webhook`, no login, CSRF exempt).
2. `integrations.services.process_webhook` asks the provider to check `X-Hub-Signature-256` (403 if wrong), parses
   the JSON, and gets `IncomingComment` objects from `provider.parse_webhook_comments`.
3. `comments.services.record_incoming_comments(platform, comments)` finds the `SocialAccount` by
   `(platform, account_external_id)`, skips unknown accounts (logged, still 200 so Meta stops retrying), and creates
   each comment once. Every newly created comment not written by the account itself queues
   `apps.agent.tasks.handle_comment` with `transaction.on_commit`. Returns the number created.
4. The view answers `200 OK`. Duplicate deliveries are no-ops thanks to the unique constraint.

The GET handshake (`hub.mode`, `hub.verify_token`, `hub.challenge`) is answered by
`integrations.services.answer_webhook_challenge`. Platform details are in `DEV-instagram.md`.

## Model: `Comment`

| Field | Notes |
|---|---|
| `account` | FK to `SocialAccount`, `related_name="comments"`, cascade |
| `external_id` | Platform comment id. Unique with `account` |
| `media_external_id` | Post or reel the comment is on |
| `parent_external_id` | Set for replies |
| `author_external_id`, `author_username` | Commenter |
| `text` | Comment text |
| `is_from_account` | True when the connected account wrote it (its own replies), so the agent can ignore them |
| `commented_at` | From the webhook entry time |
| `payload` | Raw webhook `value`, for debugging |

Registered in the Django admin (`/admin/`, search by text, username, ids).

## Files

`models.py`, `model_querysets.py` (`create_comment_if_new`, `list_comments_for_account`, `get_comment`), `services.py`, `admin.py`,
`tests.py` (handshake ok, wrong token, unset token, signed comment stored once, own comment flagged, bad signature 403,
unknown account skipped, unknown platform 404).
