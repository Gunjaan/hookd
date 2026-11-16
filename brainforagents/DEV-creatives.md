# DEV: Post studio (`backend/apps/creatives`)

The seller uploads 1 to 3 phone photos of one product, picks a look, single or carousel and optional notes, and gets a
**ready to post draft**: photorealistic 1080x1350 (4:5) photos of the same product (branding kept exactly), plus an
editable caption and hashtags, and 3 to 6 **"Teach your reply agent" questions** (price, sizes, delivery...) whose
answers go into the reply agent's brains when the post goes live. "Post now" hands the photos to the normal publish pipeline (`DEV-content.md`).

Engines are pluggable. Today **Gemini (Google AI Studio) does everything**: a cheap text model plans and writes, an
image model renders. Claude replaces the copy engine later with one new file (see "Plugging in Claude").

## Flow

1. `/studio/publishing/new/` (`CreativeForm`): photos (1 to 3, JPEG/PNG/WebP, 10 MB, must open in Pillow), `look`
   (`model`, `studio`, `lifestyle`, `flatlay`), `format_choice` (`auto`, `single`, `carousel`), `notes`.
2. `services.start_creative` checks `GEMINI_API_KEY` (`core.gemini.GeminiUnavailable`) and `CREATIVE_DAILY_LIMIT` per
   user per rolling 24 h (`CreativeDailyLimitReached`), stores each photo downscaled (`imaging.source_jpeg`: upright,
   max 1024 px, JPEG q85), creates the draft in `planning` and queues `tasks.generate_creative` on commit.
3. `generate_creative`: **one** copy engine call with the photos and `prompts.plan_request` returns the brief
   (product, branding details, audience), 1 to 5 shot plans, caption, hashtags and the reply agent questions.
   `single` keeps only the first shot. Saves the brief, caption, normalized hashtags, `brain_questions` and one `CreativeSlide` per shot (`generating`), then queues
   `tasks.render_slide` per slide on commit (one task per image keeps each under `CELERY_TASK_TIME_LIMIT`).
   A planning error marks the draft `failed` with the message.
4. `render_slide`: one image engine call with the photos and `prompts.image_prompt` (shot prompt, optional
   "Change: <tweak>", the branding list). The result is stored as a 1080x1350 JPEG (`imaging.post_jpeg`, center
   crop). After each slide `_settle` makes the draft `ready` when nothing is pending and at least one slide worked,
   `failed` when none did.
5. The draft page refreshes itself (`<meta http-equiv="refresh" content="4">`) while `planning` or `generating`.
   `expire_stale_work` (run on every view) fails planning or slides pending longer than 5 minutes (lost worker
   task), so the page never spins forever.
6. Editing: "Redo this photo" (`regenerate_slide`, optional tweak) re-renders that slide only. "Save draft"
   (`update_copy`) stores caption, hashtags and the question answers (`answer_0..5`, inputs outside the form joined
   with `form="draft-form"`). "Rewrite" (`rewrite_caption`) is one text only call from the stored
   brief, no photos resent.
7. "Post now" posts the same form to `creative-publish`: saves the edited copy, then `publish_creative` sends the
   ready slides as JPEG uploads to `content.services.create_publish_job` (`image` for one slide, `carousel` for
   more) with caption + blank line + hashtags (max 2200, `CreativeCaptionTooLong`). The draft links the job and
   becomes `published`, unless the job failed at once (then it stays `ready`). Quota, HTTPS `APP_URL` and polling
   are the content app's.
8. Brains: once the post is live, `save_brain_answers` writes each answered question as a `Label: answer` line,
   `post` scope into the post's `PostBrain` (video brain), `account` scope into `AgentProfile.knowledge` (account
   brain); an existing line with the same label is replaced (`merge_brain_lines`), unanswered questions are skipped,
   and it runs once per draft (`brain_saved_at`). Images are usually live at once, so `publish_creative` saves them
   directly; a post still processing is handled by the `content.signals.post_published` receiver
   (`receivers.py`, connected in `CreativesConfig.ready`).

## Token and cost rules

- Photos downscaled once to max 1024 px; the plan call sends them at `MEDIA_RESOLUTION_MEDIUM` (enough to read labels).
- One text call per draft does analysis, branding, shots, caption, hashtags and reply agent questions together,
  JSON schema output, `max_output_tokens` 2560 (rewrite 1024). The questions cost no extra call.
- One image call per slide, `response_modalities=["IMAGE"]` (no text back), aspect ratio `4:5`.
- Redo renders one slide; rewrite never resends photos; daily draft cap per user.
- System prompts are constants with no per request values, so providers with prompt caching can reuse the prefix
  (tested by `PromptTests`).

## Prompts (`prompts.py`)

| Name | Use |
|---|---|
| `PLAN_SYSTEM` | Creative director prompt: product fidelity first (list every branding detail with exact text, never invent or move logos), shot rules per look, single (1) or carousel (3 to 5: hero, branding close up, in use, then fit, angle or styling), 40 to 70 word concrete shot prompts, caption rules (hook under 80 chars, 1 to 2 paragraphs, one call to action, max 3 emojis, under 600 chars, notes' language, never invent prices), 8 to 15 hashtags, then 3 to 6 reply agent questions (label, question under 70 chars with an example, scope `post` or `account`, answer only when the notes state it) |
| `REWRITE_SYSTEM` | Rewrites caption and hashtags from the brief with the seller's change request, same rules |
| `PLAN_SCHEMA`, `REWRITE_SCHEMA` | JSON schemas for the two text calls |
| `plan_request`, `rewrite_request`, `image_prompt` | Per request text. `FORMAT_GUIDE` explains `auto` (single unless details or angles help, then 3) |

## Engines (`engines/`)

- `base.py`: `ProductPhoto(data, mime_type)`, `ShotPlan(role, prompt)`, `BrainQuestion(label, question, scope,
  answer)`, `PostBrief(product, branding, audience, slides, caption, hashtags, questions)`, `PostCopy(caption, hashtags)`; ABCs `CopyEngine.plan(photos, request_text)`,
  `CopyEngine.rewrite(request_text)`, `ImageEngine.render(photos, prompt) -> bytes`. Engines never touch the database.
- `gemini.py`: `GeminiCopyEngine`, `GeminiImageEngine` through `apps.core.gemini` (`generate_json`, `generate`; API
  and network errors are `GeminiError`, see `DEV-core.md`). No planned shot or no image in the answer is a
  `CreativeEngineError`. Unknown question scopes become `post`.
- `registry.py`: `get_copy_engine()`, `get_image_engine()` from the dotted path settings.

**Plugging in Claude:** add `engines/claude.py` with a `CopyEngine` that sends `prompts.PLAN_SYSTEM` (with
`cache_control`) and the photos plus request text, using structured output with `PLAN_SCHEMA`; point
`HOOKD_COPY_ENGINE` at it; add `ANTHROPIC_API_KEY` to settings, `.env.example` and `DEV-infrastructure.md`; check
the key in `start_creative` for whichever engines are configured. Images stay on Gemini.

## Settings (`config/settings.py`)

| Setting | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | env | Required for the studio |
| `HOOKD_COPY_ENGINE` | `apps.creatives.engines.gemini.GeminiCopyEngine` | Dotted path |
| `HOOKD_IMAGE_ENGINE` | `apps.creatives.engines.gemini.GeminiImageEngine` | Dotted path |
| `CREATIVE_COPY_MODEL` | env `GEMINI_COPY_MODEL`, else `gemini-3.5-flash-lite` | Text model |
| `CREATIVE_IMAGE_MODEL` | env `GEMINI_IMAGE_MODEL`, else `gemini-nano-banana-2.1` | Image model |
| `CREATIVE_DAILY_LIMIT` | `20` | New drafts per user per 24 h |

## Models

| Model | Fields |
|---|---|
| `Creative` | `user`, `status` (`planning`, `generating`, `ready`, `failed`, `published`), `look`, `format_choice`, `notes`, `brief` JSON (`product`, `branding`, `audience`), `caption`, `hashtags` ("#a #b" string), `brain_questions` JSON (list of `{label, question, scope, answer}`), `brain_saved_at`, `error`, `publish_job` (FK `content.PublishJob`, SET_NULL). Properties `is_working`, `answered_questions` |
| `CreativeSource` | `creative`, `token`, `position`, `file` (`MEDIA_ROOT/creatives/sources/<token>.jpg`) |
| `CreativeSlide` | `creative`, `position`, `role`, `prompt`, `tweak`, `status` (`pending`, `ready`, `failed`), `image` (`MEDIA_ROOT/creatives/slides/<uuid>.jpg`), `error` |

Choices: `Look`, `FormatChoice` in `models.py`. All in the Django admin (sources and slides inline).
Files are deleted with the draft (`model_querysets.delete_creative`) and when a slide is re-rendered.

## Services (`services.py`)

| Function | Does |
|---|---|
| `list_creatives(user)`, `get_user_creative` (`CreativeNotFound`), `publish_accounts(user)` | Pages |
| `start_creative`, `generate_creative`, `render_slide`, `expire_stale_work` | Flow steps 2 to 5 |
| `regenerate_slide` (`SlideNotFound`, `CreativeNotReady`), `update_copy`, `rewrite_caption`, `delete_creative` | Editing |
| `publish_creative` | Step 7 |
| `save_brain_answers(creative, post)`, `save_brain_answers_for_job(job, post)`, `merge_brain_lines(existing, lines)` | Step 8, through `agent.services` (`get_post_brain`, `save_post_brain`, `get_profile`, `update_profile`) |
| `user_file(user, kind, file_id)` | A slide image or source photo of the user's own draft |
| `format_hashtags(tags)`, `full_caption(caption, hashtags)` | Hashtags lowercased, punctuation removed, deduplicated, max 30, as "#a #b" |

`model_querysets.py`: `create_creative`, `add_source`, `list_sources`, `count_creatives_since`, `get_creative`,
`get_creative_for_user`, `list_creatives_for_user` (30 newest), `update_creative`, `save_plan`, `find_creative_for_job`, `get_slide`,
`get_slide_for_user`, `get_source_for_user`, `mark_slide_pending`, `save_slide_image`, `mark_slide_failed`,
`slide_statuses`, `ready_slides`, `delete_creative`.
`imaging.py`: `source_jpeg`, `post_jpeg` (Pillow). `receivers.py`: `post_published` receiver. Errors in `exceptions.py`.
`forms.brain_answers(data)`: the `answer_0..5` values, max 300 characters each.

## Tasks

`tasks.py`: `generate_creative(creative_id)` returns the draft status, `render_slide(slide_id)` the slide status.

## Pages (`views.py`, `urls.py`, `forms.py`, `templates/creatives/`)

| Route | Name | Page |
|---|---|---|
| `GET/POST /studio/publishing/new/` | `creative-new` | Four numbered `story-step` cards: photos (dropzone with thumbnails via app.js, tips), look cards, format cards, notes |
| `GET /studio/publishing/drafts/<id>/` | `creative-detail` | Working: progress steps and auto refresh. Ready: Instagram style preview (account, scroll-snap carousel with 1/3 badges, per slide "Redo this photo" with optional change and Download, caption preview) beside one form with caption (character counter), hashtags, account select, "Save draft" and "Post now" (`formaction`), then a "Teach your reply agent" card (one input per question, prefilled when the notes said it, "Saved for this post" or "Saved for all your posts"), then a "Rewrite" form. Posted: "Your reply agent knows" with the saved facts and a link to edit them on the post. "Delete draft" with confirm |
| `POST .../drafts/<id>/copy/`, `/rewrite/`, `/publish/`, `/delete/` | `creative-copy`, `creative-rewrite`, `creative-publish`, `creative-delete` | Back to the draft with a message; publish goes to the publishing list, delete to publishing |
| `POST /studio/publishing/slides/<id>/redo/` | `creative-slide-redo` | Queues one slide render |
| `GET /studio/publishing/files/<kind>/<id>/` (kind `slide` or `source`) | `creative-file` | Owner only JPEG; `?download=1` as attachment |

`draft_card.html` is the draft tile on `/studio/publishing/` (`content/publishing.html`: "Create a post" button,
Drafts grid or a 3 step how it works card, then Published from Hookd). Other users' drafts, slides and files give 404.

## Tests (`tests.py`)

Engines faked with `override_settings` (`FakeCopyEngine`, `FakeImageEngine`), Graph faked as in content tests.
New page steps; carousel planned with one text call, three renders with branding in the prompt, 1080x1350 JPEG,
ready page and draft card; sources downscaled to 1024 px JPEG; single keeps one slide; one failed slide still
ready; all failed marks failed; planning error shown; photo validation (count, unreadable, type); missing key;
daily limit; stale slide failed on view; redo renders only that slide with the change and no text call; rewrite
sends no photos; hashtag normalization; caption over 2200 refused; delete removes files; 404 for other users;
owner image and download; carousel publish with edited caption and hashtags; single publishes as image; not ready
cannot publish; HTTPS error shown; system prompts hold no placeholders; image prompt branding; 30 hashtag cap.
Reply agent questions: stored and shown with prefilled answers, saved with the draft, publishing writes post facts to
the video brain and shop facts to the account brain (same label replaced), a post that goes live later is taught
through the signal, label merge; plan schema asks for questions.
