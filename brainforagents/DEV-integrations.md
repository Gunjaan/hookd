# DEV: Integrations (`backend/apps/integrations`)

Platform neutral layer for connecting creator accounts. Instagram is the only provider today;
YouTube will be a second provider with no changes to models, services or views.

## Layers

```
views.py  ->  services.py  ->  providers/registry.get_provider(platform)  ->  <Provider>
                   |
                   +-> model_querysets.py  (only file that touches the ORM)
```

## Model: `SocialAccount`

| Field | Notes |
|---|---|
| `user` | FK to User, `related_name="social_accounts"` |
| `platform` | `Platform` choice: `instagram`, `youtube` |
| `external_id` | Platform account id used in webhooks. Unique with `platform` |
| `username`, `display_name`, `avatar_url` | Profile |
| `access_token`, `refresh_token` | `EncryptedTextField`. Never serialized, excluded from admin |
| `token_expires_at` | Null means no expiry |
| `scopes` | JSON list of granted permissions |
| `status` | `active`, `expired`, `revoked`, `error`; `status_detail` holds the reason |
| `metadata` | JSON, provider specific (for Instagram: `client_business_id`, `system_user_id`, `login_config_id`, `assigned_account_ids`, `followers_count`, `media_count`) |
| `last_refreshed_at` | Last time tokens were written |

An account can belong to only one Hookd user. Reconnecting by the same user updates it in place.

## Provider interface (`providers/base.py`)

```python
class BaseProvider(ABC):
    platform, display_name, description            # class attributes
    capabilities: frozenset[Capability]            # what the platform can do, shown in UI
    supports_token_refresh: bool                   # False hides "Refresh"; tokens are renewed by "Reconnect"
    is_configured() -> bool                        # credentials present
    ensure_configured()                            # raises ProviderNotConfigured (503)
    build_authorization_url(state) -> str
    exchange_code(code) -> TokenBundle             # longest lived token available
    fetch_profile(access_token) -> AccountProfile
    refresh_access_token(account) -> TokenBundle
    on_account_connected(account)                  # post connect hook; default calls subscribe_webhooks when supported
    supports_webhooks -> bool                      # property: REALTIME_WEBHOOKS in capabilities
    subscribe_webhooks(account)                    # subscribe with the stored token (Instagram: subscribed_apps)
    verify_webhook_challenge(params) -> str        # default raises WebhookVerificationError
    verify_webhook_signature(body, headers) -> bool  # default False
    parse_webhook_comments(payload) -> list[IncomingComment]  # default []
    # Replies. reply_to_comment defaults to FeatureNotSupported.
    reply_text_problems(text) -> list[str]         # why the platform would refuse a reply; default []
    reply_to_comment(account, comment_external_id, message) -> str   # public reply, returns its id
    # Content. Defaults raise FeatureNotSupported.
    list_media(account, limit, after="") -> MediaPage
    get_media(account, media_external_id) -> MediaItem
    list_comments(account, media_external_id, limit, after="") -> CommentPage
    fetch_media_insights(account, media_external_id, product_type) -> list[Metric]
    fetch_account_insights(account, days) -> list[Metric]
    publishing_quota(account) -> PublishQuota
    start_publish(account, kind, media_urls, caption) -> str   # id to poll
    publish_status(account, publish_id) -> PublishStatus
    finish_publish(account, publish_id) -> MediaItem
```

DTOs in `providers/types.py`: `TokenBundle(access_token, expires_at, refresh_token, scopes, platform_user_id)`,
`AccountProfile(external_id, username, display_name, avatar_url, metadata)`,
`IncomingComment(account_external_id, external_id, text, author_external_id, author_username, media_external_id,
parent_external_id, commented_at, payload)`, `MediaItem(external_id, media_type, product_type, caption, permalink,
media_url, thumbnail_url, posted_at, like_count, comments_count)`, `MediaPage(items, next_cursor)`,
`Metric(key, label, value, unit)`, `CommentPage(comments, next_cursor)`, `PublishStatus(state, detail)`,
`PublishQuota(used, limit)` with `remaining`. An empty `next_cursor` means there is no next page.

`Capability` (in `constants.py`): `public_reply`, `private_reply`, `direct_message`, `hide_comment`,
`publish`, `realtime_webhooks`, `insights`. Declared per provider and shown on the connections page. `insights` and
`publish` gate the studio features (`DEV-content.md`); `public_reply` gates the reply agent (`DEV-agent.md`). Private
replies, DMs and hiding comments are not implemented yet.

Other choices in `constants.py`: `MediaType` (`image`, `video`, `carousel`), `ProductType` (`feed`, `reel`, `story`),
`PublishKind` (`image`, `reel`, `carousel`), `PublishState` (`in_progress`, `finished`, `failed`).

`providers/registry.py` loads classes from `settings.HOOKD_PROVIDERS` (dotted paths). `get_provider(platform)`
raises `ProviderNotFound` (404) for unknown platforms.

### Adding a provider (for example YouTube)

1. Create `providers/youtube/` with `client.py` (HTTP + error translation) and `provider.py` (subclass `BaseProvider`).
2. Add its dotted path to `HOOKD_PROVIDERS` and its settings dict and env vars (`.env.example`, `DEV-infrastructure.md`).
3. Write `DEV-youtube.md`. The UI needs only an icon branch in `templates/integrations/platform_icon.html`
   (and a `.platform-icon--youtube` color, already present).

## OAuth state (`oauth_state.py`)

`issue_state(user_id, platform)` returns a `django.core.signing` token (salted, compressed, random nonce).
`validate_state` checks signature, 10 minute max age, and that user and platform match. Raises `OAuthStateError` (400).
No server storage; the platform's single use code prevents replay.

## Services (`services.py`)

| Function | Does |
|---|---|
| `build_connect_url(user, platform)` | Checks config, issues state, returns authorization URL |
| `complete_connection(user, platform, code, state)` | Validates state, exchanges code, fetches profile, rejects accounts owned by another user (`AccountAlreadyLinked`, 409), upserts, runs `on_account_connected` (failure is logged and saved to `status_detail`, not raised) |
| `connection_overview(user)` | `ProviderOverview(provider, configured, capabilities, accounts)` per provider, for the connections page |
| `get_user_account(user, account_id)` | `AccountNotFound` (404) if not the user's |
| `disconnect_account(user, account_id)` | Deletes the account row, returns the deleted instance |
| `refresh_account_token(account)` | Calls provider; on `TokenExpiredError` sets `expired`, on other API errors sets `error`, then re-raises |
| `subscribe_account_webhooks(account)` | Re-subscribes with the stored token. Success clears `status_detail`; `TokenExpiredError` sets `expired`; other API errors save "Webhook setup incomplete: ..." and re-raise. `WebhooksNotSupported` if the provider has no webhooks |
| `subscribe_all_webhooks(platform)` | Runs the above for every active account with webhooks; returns `SubscribeSummary(subscribed, failed)` |
| `answer_webhook_challenge(platform, params)` | Provider handshake, returns the challenge |
| `process_webhook(platform, body, headers)` | Checks the signature, parses JSON (`WebhookVerificationError` on failure), passes comments to `apps.comments.services.record_incoming_comments`, returns the number stored |
| `refresh_expiring_tokens(window, platform)` | Refreshes active accounts expiring within `window`; returns `RefreshSummary(refreshed, failed)` |

## model_querysets.py

`list_accounts_for_user`, `get_account_for_user`, `find_account`, `upsert_account`, `save_refreshed_token`,
`set_account_status`, `delete_account`, `active_accounts`, `active_accounts_expiring_before`,
`stored_access_token_ciphertext` (raw SQL read of the encrypted column, for auditing and tests).

## Errors (`exceptions.py`, all `HookdError`)

`ProviderNotFound`, `ProviderNotConfigured`, `OAuthStateError`, `AccountAlreadyLinked`, `AccountNotFound`,
`ProviderAPIError` (platform error or network failure; the client logs the HTTP status and Graph code),
`TokenExpiredError(ProviderAPIError)`, `RateLimitedError(ProviderAPIError)` (platform rate limit or temporary
policy block; the reply agent pauses the account), `WebhooksNotSupported`, `WebhookVerificationError` (webhook view answers 403),
`FeatureNotSupported` (provider method the platform does not offer). Each has a user facing `default_message`; views show `exc.message`.

## Views (`views.py`, `urls.py`)

Connections page, connect, OAuth callback, refresh, resubscribe (`account_subscribe`), disconnect, and `webhook` (no login, CSRF exempt). Routes and templates are in `DEV-web.md`.

## Management commands

| Command | Options | Purpose |
|---|---|---|
| `refresh_social_tokens` | `--within-days 10`, `--platform` | Cron job, daily |
| `subscribe_webhooks` | `--platform` | Manual: re-subscribe all active accounts after changing webhook fields |

## Tests (`tests.py`)

OAuth state round trip and tampering, tokens encrypted at rest. `ConnectionPageTests`: provider list, YouTube shown as coming soon with no connect form, login required,
connect redirect URL contents and signed state, unknown platform, missing credentials, full callback flow with mocked
Graph HTTP (token, metadata, page content, webhook subscription), failed subscription keeps the account with `status_detail`, account row shows Reconnect and hides Refresh,
reconnect updates the existing account in place, resubscribe success clears the warning, resubscribe failure is
shown and saved, resubscribe only own accounts, `subscribe_webhooks` command, no shared Instagram account, account owned by another user, cancel and
forged state, login redirect keeps the callback query, disconnect isolation between users.
