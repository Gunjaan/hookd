# DEV: Post studio (`backend/apps/creatives`)

The seller uploads 1 to 3 phone photos of one product, picks a look, single or carousel and optional notes, and gets a
**ready to post draft**: photorealistic 1080x1350 (4:5) photos of the same product (branding kept exactly), plus an
editable caption and hashtags, and 3 to 6 **"Teach your reply agent" questions** (price, sizes, delivery...) whose
answers go into the reply agent's brains when the post goes live. "Post now" hands the photos to the normal publish pipeline (`DEV-content.md`).

Engines are pluggable. **Claude Haiku plans and writes** (it reads the product photos), **Gemini (Google AI Studio)
renders** the photos, since Claude cannot generate images.

## Flow

1. `/studio/publishing/new/` (`CreativeForm`): photos (1 to 3, JPEG/PNG/WebP, 10 MB, must open in Pillow), `look`
   (`model`, `studio`, `lifestyle`, `flatlay`), `format_choice` (`auto`, `single`, `carousel`), `notes`.
2. `services.start_creative` checks `ANTHROPIC_API_KEY` (`ai.claude.ClaudeUnavailable`), `GEMINI_API_KEY`
   (`ai.gemini.GeminiUnavailable`) and `CREATIVE_DAILY_LIMIT` per
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
5. While `planning` or `generating`, `app.js` polls `api-draft-progress` every 4 s (step 6). Leaving `planning`
   reloads the page once (the whole draft layout appears).
   `expire_stale_work` (run on every view) fails planning or slides pending longer than 5 minutes (lost worker
   task), so the page never spins forever.
   The draft page opens with the draft story (`views.draft_story`, `creatives/story.html`): five steps, Your photo
   (the first `CreativeSource` as its marker), Hookd made photos, Caption and hashtags, Posted, Hooky knows it, each
   `done`, `active` (where the draft is now, `aria-current="step"`) or `todo` with a one line detail, so the seller
   always sees where the post stands and what comes next.
6. Editing happens **in place, no page reload** (`api.py`, `[data-draft]` in `app.js`), so edits are never wiped.
   Buttons carry `data-draft-action="<api url>"` and POST the `#draft-form` fields (caption, hashtags, answers
   `answer_0..5` joined with `form="draft-form"`, rewrite instruction); a redo adds its slide's change text as `tweak`.
   Every button is `type="button"`, so Enter in a field submits nothing. "Redo this photo" (`regenerate_slide`)
   re-renders that slide only; the current
   image is kept as a `CreativeSlideVersion` first (the version takes over the file, nothing is copied), so a failed
   redo still has the old photo. "Earlier versions (n)" under each slide shows them with Download and "Use this one"
   (`use_slide_version`, no AI call: the images swap and the replaced one becomes a version). Redo and "Use this one"
   show only when the draft is `ready` or `failed`. "Save draft" (`update_copy`) stores the copy and writes the
   normalized values back. "Rewrite" (`rewrite_caption`) saves the posted copy and answers, then makes one text only
   call from the stored brief and the edited caption, no photos resent.
   Draft state (`views.draft_state`, used by the page and the endpoints): `status`, `title`, `working`, `can_post`,
   `alert` (`kind`, `text`), `story` (the story strip html, swapped into `[data-draft-story]`) and `slides` (`id`, `key`,
   `html` from `creatives/slide.html`). The page swaps a slide only
   when its `key` (status, update time, versions, whether photos can change) differs, and toggles "Post now".
7. "Post now" is the one real form post (`#draft-form` action `creative-publish`, submitted by app.js): saves the edited copy, then `publish_creative` sends the
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

- Photos downscaled once to max 1024 px before the plan call (enough to read labels, few image tokens).
- One text call per draft does analysis, branding, shots, caption, hashtags and reply agent questions together,
  structured JSON output at low effort, `max_output_tokens` 8000 (rewrite 4000; ceilings with room for the short
  thinking). The questions cost no extra call.
- One image call per slide, `response_modalities=["IMAGE"]` (no text back), aspect ratio `4:5`.
- Redo renders one slide; rewrite never resends photos; going back to an earlier photo costs nothing; redo controls
  are hidden while anything renders; daily draft cap per user.
- **Every AI call is recorded** (`ai.AiCall`, see `DEV-ai.md`): plan (`creative_plan`), each render
  (`creative_photo`, also failed ones) and rewrite (`caption_rewrite`), each with the draft owner and the draft as
  subject. The `Creative` admin list shows tokens and cost per draft and links to its calls.
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
  answer)`, `PostBrief(product, branding, audience, slides, caption, hashtags, questions)`, `PostCopy(caption, hashtags)`,
  `RenderedImage(data)`; ABCs `CopyEngine.plan(photos, request_text)`,
  `CopyEngine.rewrite(request_text)`, `ImageEngine.render(photos, prompt) -> RenderedImage`. Engines never touch the database.
- `claude.py`: `ClaudeCopyEngine` through `apps.ai.claude.generate_json` (photos as base64 image blocks, then the
  request text; errors are `ClaudeError`, see `DEV-ai.md`). The schema has no array limits (Claude structured
  output does not support them), so the engine caps slides at 5, hashtags at 15 and questions at 6. Unknown question
  scopes become `post`. No planned shot is a `CreativeEngineError`.
- `gemini.py`: `GeminiImageEngine` through `apps.ai.gemini.generate` (errors are `GeminiError`). No image in the
  answer is a `CreativeEngineError`.
- `registry.py`: `get_copy_engine()`, `get_image_engine()` from the dotted path settings.

## Settings (`config/settings.py`)

| Setting | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | env | Required for the studio (copy) |
| `GEMINI_API_KEY` | env | Required for the studio (photos) |
| `HOOKD_COPY_ENGINE` | `apps.creatives.engines.claude.ClaudeCopyEngine` | Dotted path |
| `HOOKD_IMAGE_ENGINE` | `apps.creatives.engines.gemini.GeminiImageEngine` | Dotted path |
| `CREATIVE_COPY_MODEL` | env `CLAUDE_COPY_MODEL`, else `claude-haiku-5-5` | Text model |
| `CREATIVE_IMAGE_MODEL` | env `GEMINI_IMAGE_MODEL`, else `gemini-nano-banana-2.1` | Image model |
| `CREATIVE_DAILY_LIMIT` | `20` | New drafts per user per 24 h |
| `AI_MODEL_PRICES` | copy and image model prices | USD per million input and output tokens per model name (`DEV-ai.md`) |

## Models

| Model | Fields |
|---|---|
| `Creative` | `user`, `status` (`planning`, `generating`, `ready`, `failed`, `published`), `look`, `format_choice`, `notes`, `brief` JSON (`product`, `branding`, `audience`), `caption`, `hashtags` ("#a #b" string), `brain_questions` JSON (list of `{label, question, scope, answer}`), `brain_saved_at`, `error`, `publish_job` (FK `content.PublishJob`, SET_NULL). Properties `is_working`, `answered_questions` |
| `CreativeSource` | `creative`, `token`, `position`, `file` (`MEDIA_ROOT/creatives/sources/<token>.jpg`) |
| `CreativeSlide` | `creative`, `position`, `role`, `prompt`, `tweak`, `status` (`pending`, `ready`, `failed`), `image` (`MEDIA_ROOT/creatives/slides/<uuid>.jpg`), `error` |
| `CreativeSlideVersion` | `slide` (`versions`), `image`, `tweak` (the change that made it). Newest first |

Choices: `Look`, `FormatChoice` in `models.py`. All in the Django admin (sources and slides inline on the draft, a link to its AI calls, versions inline on the slide).
Files (sources, slides, versions) are deleted with the draft (`model_querysets.delete_creative`); a redo keeps the old file as a version.

## Services (`services.py`)

| Function | Does |
|---|---|
| `list_creatives(user)`, `get_user_creative` (`CreativeNotFound`), `publish_accounts(user)` | Pages |
| `start_creative`, `generate_creative`, `render_slide`, `expire_stale_work` | Flow steps 2 to 5 |
| `regenerate_slide` (`SlideNotFound`, `CreativeNotReady`), `use_slide_version` (`SlideVersionNotFound`), `update_copy`, `rewrite_caption`, `delete_creative` | Editing. Redo, swap and rewrite take `copy` (caption, hashtags, answers) and save it first (`_save_copy`). Editable in `ready`, `generating` and `failed` with a brief |
| `publish_creative` | Step 7 |
| `save_brain_answers(creative, post)`, `save_brain_answers_for_job(job, post)`, `merge_brain_lines(existing, lines)` | Step 8, through `agent.services` (`get_post_brain`, `save_post_brain`, `get_profile`, `update_profile`) |
| `user_file(user, kind, file_id)` | A slide image, earlier version or source photo of the user's own draft |
| `format_hashtags(tags)`, `full_caption(caption, hashtags)` | Hashtags lowercased, punctuation removed, deduplicated, max 30, as "#a #b" |

`model_querysets.py`: `create_creative`, `add_source`, `list_sources`, `count_creatives_since`, `get_creative`,
`get_creative_for_user`, `list_creatives_for_user` (30 newest), `update_creative`, `save_plan`, `find_creative_for_job`, `get_slide`,
`get_slide_for_user`, `get_source_for_user`, `mark_slide_pending` (archives the current image), `save_slide_image`, `mark_slide_failed`,
`get_version_for_user`, `swap_version_into_slide`, `slide_statuses`, `ready_slides`, `delete_creative`.
`imaging.py`: `source_jpeg`, `post_jpeg` (Pillow). `receivers.py`: `post_published` receiver. Errors in `exceptions.py`.
`forms.brain_answers(data)`: the `answer_0..5` values, max 300 characters each.

## Tasks

`tasks.py`: `generate_creative(creative_id)` returns the draft status, `render_slide(slide_id)` the slide status.

## Pages and JSON endpoints (`views.py`, `api.py`, `urls.py`, `forms.py`, `templates/creatives/`)

| Route | Name | Page |
|---|---|---|
| `GET/POST /studio/publishing/new/` | `creative-new` | Four numbered `story-step` cards: photos (dropzone with thumbnails via app.js, tips), look cards, format cards, notes |
| `GET /studio/publishing/drafts/<id>/` | `creative-detail` | The draft story strip on top (the first step's photo and its "View" link open the uploads in the photo viewer). Planning: progress steps. Planning and generating: polls `api-draft-progress`. Ready: Instagram style preview (account, carousel with 1/3 badges, previous and next buttons over the photo and dots (`.carousel`), per slide "Redo this photo" with optional change, Download and "Earlier versions", caption preview) beside one form with caption (character counter), hashtags, account select, "Save draft" (JSON) and "Post now" (form post), then a "Teach your reply agent" card (one input per question, prefilled when the notes said it, "Saved for this post" or "Saved for all your posts"), then a "Rewrite" field (JSON, sends the draft form). Posted: the header has "View post" (the post modal) and "Instagram" (permalink) when the job's post is linked; the side shows "Post status" inline (`content/job_item.html`: status badge, error, "Check status" while processing, "View post" in the modal) instead of sending the seller to the publishing list, then "What Hooky knows" with the saved answers and "Add or edit facts" (post modal), or "Teach Hooky" (agent page) when the post link is gone. "Delete draft" with confirm (the post stays on Instagram) |
| `POST .../drafts/<id>/publish/`, `/delete/` | `creative-publish`, `creative-delete` | Publish saves the copy and goes to the publishing list, delete goes to publishing |
| `GET /api/drafts/<id>/progress/` | `api-draft-progress` | Draft state JSON (runs `expire_stale_work`) |
| `POST /api/drafts/<id>/copy/` | `api-draft-copy` | Save draft: `{caption, hashtags, full_caption, message}` |
| `POST /api/drafts/<id>/rewrite/` | `api-draft-rewrite` | Saves the copy, rewrites: same answer as copy |
| `POST /api/slides/<id>/redo/` | `api-slide-redo` | `tweak`, queues one slide render, answers the draft state |
| `POST /api/versions/<id>/use/` | `api-version-use` | Puts an earlier photo back, answers the draft state and `message` |
| `GET /studio/publishing/files/<kind>/<id>/` (kind `slide`, `version` or `source`) | `creative-file` | Owner only JPEG; `?download=1` as attachment |

`draft_card.html` is the draft tile on `/studio/publishing/` (`content/publishing.html`: "Create a post" button,
Drafts grid or a 3 step how it works card, then Published from Hookd). Other users' drafts, slides and files give 404.

## Tests (`tests.py`)

Engines faked with `override_settings` (`FakeCopyEngine`, `FakeImageEngine`), Graph faked as in content tests.
New page steps; carousel planned with one text call, three renders with branding in the prompt, 1080x1350 JPEG,
ready page and draft card; sources downscaled to 1024 px JPEG; single keeps one slide; one failed slide still
ready; all failed marks failed; planning error shown; photo validation (count, unreadable, type); missing keys (each);
daily limit; stale slide failed on view; redo renders only that slide with the change and no text call; rewrite
sends no photos; hashtag normalization; caption over 2200 refused; delete removes files; 404 for other users;
owner image and download; carousel publish with edited caption and hashtags; single publishes as image; not ready
cannot publish; HTTPS error shown; system prompts hold no placeholders; image prompt branding; 30 hashtag cap.
Reply agent questions: stored and shown with prefilled answers, saved with the draft, publishing writes post facts to
the video brain and shop facts to the account brain (same label replaced), a post that goes live later is taught
through the signal, label merge; plan schema asks for questions.
`ClaudeCopyEngineTests` (model call mocked): photos sent as image blocks before the text, slides, hashtags and
questions capped, unknown scope becomes `post`.
Edits and history (JSON endpoints): redo answers the working draft state and hides redo while rendering, redo while
rendering is refused, redo leaves the copy alone, rewrite keeps answers and uses the edited caption; redo keeps the old
photo as a version (also when the redo fails); "Use this one" swaps back with no AI call; progress reports state and
fails a stale slide; save returns normalized copy; too long caption 400; login (401) and CSRF (403) enforced;
originals shown; page wired to the endpoints with no meta refresh; other users get 404 for drafts, slides and versions. Usage (fake engines record like the clients): an `AiCall` per plan, render and
rewrite with the purpose, owner, draft and cost from `AI_MODEL_PRICES`, failed render recorded, unknown model costs 0, admin totals.
