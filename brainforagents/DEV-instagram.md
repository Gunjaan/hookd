# DEV: Instagram (`backend/apps/integrations/providers/instagram`)

Uses the **Instagram API with Unified Login** (graph.facebook.com). Creators log in with Instagram or
Facebook on a Meta hosted page (Facebook Login for Business with an "Instagram Login Compatible"
configuration). Works for Instagram professional accounts (Creator or Business). No Facebook Page needed.

## Files

| File | Contents |
|---|---|
| `client.py` | `InstagramClient`: `requests.Session`, timeouts, `get`, `post` (form body), `graph_url(path)` (always versioned). `LOGIN_DIALOG_URL`. Translates errors: Graph code 190 becomes `TokenExpiredError`; codes 4, 17, 32, 613 (rate limits), 368 (temporarily blocked) and HTTP 429 become `RateLimitedError`; everything else `ProviderAPIError`. Network failures become `ProviderAPIError` |
| `provider.py` | `InstagramProvider(BaseProvider)` |

## Settings (`settings.INSTAGRAM`)

| Env var | Default | Notes |
|---|---|---|
| `INSTAGRAM_APP_ID` | | **Meta app ID** (App settings > Basic, also in the dashboard URL). Not the Instagram app ID |
| `INSTAGRAM_APP_SECRET` | | **Meta app secret** (App settings > Basic) |
| `INSTAGRAM_LOGIN_CONFIG_ID` | | Configuration ID from "API setup with Unified login" > 1. Configure your app. Not a secret |
| `INSTAGRAM_REDIRECT_URI` | `{APP_URL}/auth/instagram/callback` | Must be registered under that step's "Redirect URL". HTTPS only |
| `INSTAGRAM_GRAPH_API_VERSION` | `v27.0` | Used for every Graph call |
| `INSTAGRAM_WEBHOOK_VERIFY_TOKEN` | | Random string, same value as "Verify token" in "3. Configure webhooks". Empty rejects every handshake |
| `INSTAGRAM_WEBHOOK_FIELDS` | `comments` | Comma separated fields every connected account is subscribed to (`settings.INSTAGRAM["WEBHOOK_FIELDS"]`). Add `messages` to receive DMs, story replies and story mentions (`DEV-messaging.md`), then run `subscribe_webhooks` |

Permissions are not sent by Hookd: they come from the login configuration (required `instagram_basic`,
`instagram_manage_messages`; Hookd also needs `instagram_manage_comments`, `instagram_content_publish`).

`is_configured()` requires app id, secret, config id and redirect URI. Otherwise the UI shows "Not configured"
and connect returns 503.

## OAuth and token lifecycle

1. `build_authorization_url`: `https://www.facebook.com/dialog/oauth?client_id&config_id&redirect_uri&response_type=code&override_default_response_type=true&prompt=login&state`.
   `prompt=login` makes Meta always show both "Log in with Instagram" and "Log in with Facebook"; without it a browser
   signed in to Facebook skips the Instagram option and then requires the Instagram account to already be in a business portfolio.
   Cancel comes back as `?error=access_denied`, which the callback page already handles.
2. `exchange_code`: strips any `#...` suffix, then `GET https://graph.facebook.com/{version}/oauth/access_token`
   (client_id, client_secret, redirect_uri, code). Returns a **Business Integration System User Access Token**
   scoped to the creator's business portfolio. `expires_in` missing or 0 means it never expires (`token_expires_at` null).
3. `fetch_profile`:
   - `GET /{version}/me?fields=id,client_business_id` (`/me` is the system user, not the Instagram account).
   - `GET /{version}/me/assigned_instagram_accounts?fields=id,username`. Empty list raises `ProviderAPIError` (502).
     The **first** account is connected; all ids go to `metadata.assigned_account_ids`.
   - `GET /{version}/{ig_id}?fields=id,username,name,profile_picture_url,followers_count,media_count`.
   `external_id` = Instagram professional account id. Metadata: `client_business_id`, `system_user_id`,
   `login_config_id`, `assigned_account_ids`, `followers_count`, `media_count`.
4. `refresh_access_token`: not supported by this flow. It raises `TokenExpiredError`; an expired token is noticed
   on the next API call, which marks the account `expired`, and the creator reconnects. Pick a configuration whose token never expires
   (shown in the configuration preview) so this never triggers. The token also dies if the creator removes
   the app from their business portfolio.

   `supports_token_refresh = False`, so the account row shows only "Reconnect" (one click through the Meta dialog).
5. `subscribe_webhooks` (run by the base `on_account_connected`, the "Resubscribe" button and the
   `subscribe_webhooks` command): `POST /{version}/{ig_id}/subscribed_apps` with `subscribed_fields=<INSTAGRAM_WEBHOOK_FIELDS>` and the
   account token. Must return `{"success": true}`, otherwise `ProviderAPIError` (logged into `status_detail`, the
   account stays connected). No Meta login is needed because the stored token never expires.

## Webhooks

| Method | Does |
|---|---|
| `verify_webhook_challenge(params)` | `hub.mode=subscribe` and `hub.verify_token` equal to `INSTAGRAM_WEBHOOK_VERIFY_TOKEN` (constant time) returns `hub.challenge`; anything else, or an empty configured token, raises `WebhookVerificationError` |
| `verify_webhook_signature(body, headers)` | `X-Hub-Signature-256: sha256=<hex>` must equal HMAC SHA256 of the raw body keyed with `INSTAGRAM_APP_SECRET` (the Meta app secret) |
| `parse_webhook_comments(payload)` | `object=instagram`; each `entry[].changes[]` with `field=comments` becomes an `IncomingComment`. Unified Login shape: `value.comment_id` (falls back to `value.id`), `text`, `from.id/username`, `media.id`, `parent_id`; `entry.id` is the Instagram account id, `entry.time` the timestamp |
| `parse_webhook_messages(payload)` | `object=instagram`; each `entry[].messaging[]` event with a `message.mid` becomes an `IncomingMessage` (deleted messages, reactions, reads and postbacks are skipped). `message.is_echo` means the account sent it, so the participant is `recipient.id`, otherwise `sender.id`. Kind: `reply_to.story` (id, url) is a story reply, an attachment of type `story_mention` a story mention, `is_unsupported` unsupported, other attachments media, else text. `timestamp` is in milliseconds |

Fields come from `INSTAGRAM_WEBHOOK_FIELDS` (default `comments`). After changing it, run `python manage.py subscribe_webhooks`. Meta notes: comments need Advanced Access in production and the app
must be Live to deliver real events for app users; no events for private accounts; retries for up to 36 hours, so
handling is idempotent.

## Meta app setup (Standard Access, hackathon)

1. Convert the test Instagram account to Creator or Business (Instagram app > Settings > Account type).
2. developers.facebook.com > your app > Use cases > Instagram API > Customize > **API setup with Unified login**.
3. Step 1 "Configure your app": select the "Instagram API (Unified Login)" configuration, copy its
   Configuration ID into `INSTAGRAM_LOGIN_CONFIG_ID`, and register the redirect URI (exactly `INSTAGRAM_REDIRECT_URI`).
4. App settings > Basic: copy App ID and App secret into `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET`.
5. App roles > Roles > add the Instagram account as an **Instagram Tester**; accept in the Instagram app
   (Settings > Apps and websites > Tester invites) or on instagram.com.
6. Only tester accounts can connect until App Review and Business Verification (Advanced Access).
7. DMs only work when the creator turns on "Allow access to messages" (shown during Instagram login).
8. "3. Configure webhooks": Callback URL `https://<host>/webhooks/instagram/` (trailing slash), Verify token =
   `INSTAGRAM_WEBHOOK_VERIFY_TOKEN`, "Verify and save", then subscribe to the `comments` field (and `messages` for DMs and story replies, see "Webhook fields" below).

## Local development

Meta requires HTTPS redirect URIs. Tunnel the Django server, for example `ngrok http 8000`, then set
`INSTAGRAM_REDIRECT_URI=https://<host>/auth/instagram/callback`, add `<host>` to `DJANGO_ALLOWED_HOSTS` and
`https://<host>` to `DJANGO_CSRF_TRUSTED_ORIGINS` (form posts fail CSRF otherwise), add the URI in Meta, restart
the container (env changes need it), and open the site through the tunnel URL (the session cookie belongs to that origin).

ngrok's free tier shows a warning page on the first page load. Click "Visit Site" once per browser; ngrok then
remembers it with a cookie.

## Content, insights and publishing

All calls use the stored account token. Field and metric lists are constants at the top of `provider.py`.

| Method | Graph call |
|---|---|
| `list_media` | `GET /{ig_id}/media?fields=id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count&limit&after`. `media_type` IMAGE/VIDEO/CAROUSEL_ALBUM and `media_product_type` FEED/REELS/STORY map to `MediaType`/`ProductType`. The next cursor is `paging.cursors.after`, used only when `paging.next` exists |
| `get_media` | `GET /{media_id}?fields=<same fields as list_media>`. Used for posts we learn about from a comment, and by `finish_publish` |
| `list_comments` | `GET /{media_id}/comments?fields=id,text,timestamp,username,from,parent_id&limit&after`. Top level comments only |
| `fetch_media_insights` | `GET /{media_id}/insights?metric=...`. Feed: views, reach, likes, comments, saved, shares, total_interactions. Reels add `ig_reels_avg_watch_time`, `ig_reels_video_view_total_time` (ms). Stories: views, reach, replies, shares, total_interactions. Values from `values[0].value` |
| `fetch_account_insights` | `GET /{ig_id}/insights?metric=views,reach,accounts_engaged,total_interactions&period=day&metric_type=total_value&since&until`. Values from `total_value.value` |
| `publishing_quota` | `GET /{ig_id}/content_publishing_limit?fields=config,quota_usage` (100 API posts per 24 hours) |
| `start_publish` | `POST /{ig_id}/media`: image `image_url`; reel `media_type=REELS&video_url`; carousel one `image_url&is_carousel_item=true` container per image, then `media_type=CAROUSEL&children=<ids>`. Caption on the top container. Story photo `media_type=STORIES&image_url`, story video `media_type=STORIES&video_url`, no caption (the API cannot add stickers, links or polls) |
| `publish_status` | `GET /{container_id}?fields=status_code,status`. FINISHED or PUBLISHED, IN_PROGRESS, ERROR or EXPIRED (failed, `status` is the reason) |
| `finish_publish` | `POST /{ig_id}/media_publish` with `creation_id`, then `get_media` |

Needs `instagram_manage_insights` and `instagram_content_publish` in the login configuration. Image URLs must be JPEG.
Meta fetches uploads from `APP_URL`; with a free ngrok tunnel the browser warning page only affects browsers, but
if Meta reports it cannot fetch the media, check the tunnel URL in `APP_URL` is current.

## Comment replies

| Method | Does |
|---|---|
| `reply_to_comment` | `POST /{comment_id}/replies` with `message` and the account token; returns the new comment id (missing id raises `ProviderAPIError`). Needs `instagram_manage_comments` in the login configuration |
| `reply_text_problems` | Instagram refuses comments that are empty, over 300 characters (`REPLY_MAX_LENGTH`), with more than 4 hashtags, more than 1 URL, or written entirely in capitals (checked when there are more than 3 letters) |

The connected account's own replies come back through the `comments` webhook flagged `is_from_account`, so the agent
never answers itself. Sending limits and pauses are in `DEV-agent.md`.

## Webhook fields (Meta app, "3. Configure webhooks")

| Field | Use in Hookd |
|---|---|
| `comments` | On. New comments for the reply agent |
| `messages` | Turn on (and add to `INSTAGRAM_WEBHOOK_FIELDS`). DMs, story replies, story mentions and the account's own sent messages (echoes), stored by `apps.messaging` |
| `messaging_postbacks` | Turn on before auto DMs use buttons or ice breakers (taps arrive here). Not parsed yet |
| `story_insights` | On in the Meta app. Final story metrics when a story expires. Not parsed yet (story insights are fetched on the post page) |
| `mentions` | Later: the account @mentioned in someone else's caption or comment. Story mentions come through `messages` |
| `messaging_referral` | Later: ig.me links with a `ref` and click to message ads |
| `messaging_seen`, `message_reactions`, `message_edit` | Not needed |
| `messaging_handover`, `standby` | Only when another app (or Meta's inbox automation) shares the inbox through the handover protocol |
| `live_comments` | Not needed (live videos) |

Meta rules for the next steps: replies to a DM or story reply only within 24 hours of the person's last message
(`Conversation.reply_window_ends_at`); no cold DMs; a private reply to a comment (`POST /{ig_id}/messages` with
`recipient.comment_id`) is one message per comment within 7 days of it. Messaging needs `instagram_manage_messages`
with Advanced Access, and the creator must allow message access under Instagram settings, Messages, Connected tools.

## Not built yet

Sending DMs and private replies, auto replies to DMs and story replies, `messaging_postbacks` and `story_insights`
parsing, hiding comments, video carousels.
