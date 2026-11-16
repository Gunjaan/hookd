# DEV: Content (`backend/apps/content`)

The creator studio: recent posts, insights, comments and publishing for connected accounts. All platform calls go
through the provider interface (`DEV-integrations.md`); Instagram details are in `DEV-instagram.md`.

## Lazy loading

Every page asks the platform for as little as possible.

| Where | Loads | When | Platform calls |
|---|---|---|---|
| Dashboard `/studio/accounts/<id>/` | 10 newest posts with like and comment counts, plus 7 day account insights | Recent posts come from the cache for 5 minutes (`RECENT_POSTS_TTL`), insights for 1 hour (`INSIGHTS_TTL`). "Refresh" skips the posts cache | 1 media call, 1 account insights call |
| Older posts `?after=<cursor>` | The next 10 posts | Every request (live) | 1 media call |
| Post detail `/studio/posts/<id>/` | That post's metrics | When `insights_fetched_at` is missing or older than 1 hour; "Refresh" forces it | 1 insights call |
| A comment on a post we have not stored (posted in the Instagram app) | That one post, via `services.ensure_post` | Once per post, from the agent's `handle_comment` task. Clears the recent posts cache so the dashboard shows it. A failed fetch is logged and the agent carries on without the post | 1 media call |
| Comments `/studio/posts/<id>/comments/` | 25 top level comments (`COMMENTS_PAGE_SIZE`) | Only when the creator presses "Show comments" or "Load more comments" | 1 comments call per page |

The post detail page never fetches comments. Fetched comments are saved to `apps.comments.Comment`
(`create_comment_if_new`, so webhook and API copies never duplicate) to give the agent the history.

"Load more" links work without JS (they open the next page). `static/js/app.js` enhances any
`<a data-load-more="<list id>">`: it fetches the link, appends the children of the element with that id from the
response to the local list, and swaps in the response's next link (or removes it). The post page uses this to pull the
comments page into an empty `#comment-list`.

Recent posts sync: the cache holds `{ids, next}` per account (`content:recent-posts:<account pk>`). A sync upserts the
page into `Post` and deletes cached posts newer than the oldest returned one that the platform no longer returns
(deleted on the platform). Account insights are cached under `content:account-insights:<account pk>`.

A `TokenExpiredError` from any call marks the account `expired` (with the message) and the page shows the error.

## Models

### `Post`

| Field | Notes |
|---|---|
| `account` | FK to `SocialAccount`, `related_name="posts"`. Unique with `external_id` |
| `external_id` | Platform media id |
| `media_type` | `MediaType`: `image`, `video`, `carousel` |
| `product_type` | `ProductType`: `feed` ("Post"), `reel`, `story` |
| `caption`, `permalink` | |
| `media_url`, `thumbnail_url` | Platform CDN URLs. They expire, so every sync rewrites them. `preview_url` property picks the thumbnail for videos |
| `posted_at`, `like_count`, `comments_count` | Counts are null when the platform hides them |
| `insights` | JSON list of `{key, label, value, unit}` (`unit` is `count` or `milliseconds`) |
| `insights_fetched_at`, `synced_at` | |

### `PublishJob`

`account` (`related_name="publish_jobs"`), `kind` (`PublishKind`: `image`, `reel`, `carousel`), `caption`,
`status` (`processing`, `published`, `failed`), `publish_id` (platform container id), `error`, `post` (FK set when
live), `published_at`.

### `PublishAsset`

One uploaded file of a job: `job`, `token` (UUID, unique), `position`, `file` (stored at
`MEDIA_ROOT/publish/<token>.<ext>`), `content_type`. Deleted (file and row) as soon as the job is published or fails.

## Publishing flow

The platform downloads media from a public URL, so uploads are served by the `publish-asset` view at
`{APP_URL}/publish-media/<token>/`: no login, unguessable token, and only while the job is `processing`.
`APP_URL` must start with `https://` (the tunnel URL in development), otherwise `PublishingUnavailable`.

1. `/studio/accounts/<id>/compose/` validates with `ComposeForm`: image is 1 JPEG up to 8 MB, carousel 2 to 10
   JPEGs, reel 1 MP4 or MOV up to 300 MB, caption up to 2200 characters.
2. `services.create_publish_job` checks `Capability.PUBLISH`, the HTTPS `APP_URL` and the remaining quota
   (`PublishQuotaReached`), stores the job and assets, then calls `provider.start_publish` with the asset URLs.
   A platform error marks the job failed.
3. `advance_publish_job` runs right away: `publish_status` FINISHED calls `finish_publish`, upserts the new `Post`,
   deletes the assets and clears the recent posts cache. FAILED marks the job failed with the platform's reason.
   IN_PROGRESS leaves it processing (videos take minutes).
4. While a job is processing, `schedule_publish_poll` queues the Celery task `poll_publish_job` (after the
   transaction commits) every `PUBLISH_POLL_INTERVAL` (20 s); each run calls `advance_publish_job` and re-queues
   itself while still processing. The "Check status" button advances a job immediately. A job still processing
   after `PUBLISH_TIMEOUT` (1 hour) fails.

Carousels are images only for now (video children need their own processing wait).

The post studio (`DEV-creatives.md`) also publishes through `create_publish_job`: its drafts post as `image` or
`carousel` jobs. `views.announce_publish_job(request, job)` is the shared success, failure or processing message.
`signals.post_published` (kwargs `job`, `post`) is sent by `advance_publish_job` once a job is live, so other apps
react without content importing them (the post studio saves its reply agent answers).
The publishing page lists the user's drafts (`creatives.services.list_creatives`) above the jobs.

## Services (`services.py`)

| Function | Does |
|---|---|
| `studio_accounts(user)` | The user's accounts |
| `get_user_post(user, post_id)` | `PostNotFound` if not the user's |
| `recent_posts(account, force=False)` | Cached first page, returns `PostPage(posts, next_cursor)` |
| `older_posts(account, after)` | Live page after a cursor |
| `account_insights(account)` | Cached 7 day metrics, `[]` without `Capability.INSIGHTS` |
| `post_insights(post, force=False)` | Fetches and saves stale metrics |
| `post_comments(post, after="")` | One page, stores new comments, returns `CommentList(comments, next_cursor)` |
| `can_publish(account)`, `remaining_publish_quota(account)` | |
| `create_publish_job(account, kind, caption, uploads)` | See flow above |
| `advance_publish_job(job)`, `advance_processing_jobs()` | Returns the job / `PublishSummary(published, failed, processing)` |
| `schedule_publish_poll(job)`, `poll_publish_job(job_id)` | Queue the next status check on commit / Celery entry point |
| `list_publish_jobs(user)`, `get_user_publish_job(user, job_id)`, `find_servable_asset(token)` | |

`model_querysets.py`: `upsert_post`, `posts_in_order`, `delete_posts_missing_since`, `get_post_for_user`,
`save_post_insights`, `create_publish_job`, `create_publish_asset`, `set_publish_id`, `mark_publish_job_published`,
`mark_publish_job_failed`, `delete_publish_assets`, `list_publish_assets`, `processing_publish_jobs`,
`get_publish_job`, `list_publish_jobs_for_user`, `get_publish_job_for_user`, `find_servable_asset`, `find_post`
and `latest_posts` (used by the reply agent).

Errors (`exceptions.py`): `PostNotFound`, `PublishJobNotFound`, `PublishingUnavailable`, `PublishQuotaReached`.

## Templates and filters

The post detail page also shows the **Video brain** card from the reply agent: a `PostBrainForm` (facts and the per post
agent switch) posting to `agent-post-brain` (`DEV-agent.md`). Facts there let the agent answer this post's comments
on its own.

`templates/content/`: `dashboard.html`, `post_card.html`, `metric_tiles.html`, `post_detail.html`,
`post_comments.html`, `comment_item.html`, `compose.html`, `publishing.html`.
`templatetags/content_ui.py`: `metric_display` (counts with separators, durations as `6.5s`, `2m 5s`, `1h 3m`),
`job_tone` (badge tone per job status). Views pass `nav_section` (`studio` or `publishing`) for the sidebar.

## Task and management command

`tasks.py`: `poll_publish_job(job_id)` (see the flow above). Returns the job status, stored as the task result.
`process_publish_jobs`: manual recovery, advances every processing job at once (for example after the Redis queue
was wiped). Not scheduled.

## Tests (`tests.py`)

Graph HTTP is faked with `FakeGraph`. `ensure_post`: unknown post fetched, stored and cache cleared, known post makes no call, empty id, Graph error returns None, expired token marks the account. Dashboard: redirect to first account, no accounts goes to connections, 10 posts
with counts and insights then served from cache, last page has no load more, cursor paging, deleted posts pruned,
refresh bypasses cache, expired token marks the account, other user's account 404. Post: insights cached then
refreshed, reel watch time, insights error still renders, post page makes no calls, comments page of 25 stored once,
other user's post 404. Publishing: quota shown, image publishes at once, carousel children then parent, reel waits then
the Celery poll re-queues and publishes and the asset stops being served, recovery command, container error, upload rejected, HTTP `APP_URL` refused, quota
reached, form validation, check status only own jobs, stuck job times out. `metric_display` formats.
