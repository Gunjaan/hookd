# DEV: Web UI (Django templates)

The UI is plain HTML, CSS and JS: server rendered Django templates with session auth, plus `fetch` calls to JSON
endpoints for lazy loading, polling and in-page actions. JavaScript is assumed. No JavaScript framework, no build step.
Views are thin: validate input, call `services.py`, render a template, redirect, or answer JSON.

## JSON endpoints

JSON views live in `apps/<name>/api.py` with `/api/...` routes in the app's `urls.py`. They use `apps.core.http`:
`api_login_required` (401 JSON when logged out), `json_ok(**data)`, `json_error(message, status=400)` for a
`HookdError` (`{"error": message}`); other users' objects are 404. Session auth and CSRF stay on: `app.js` `api()`
sends the `csrftoken` cookie as `X-CSRFToken`. Rich repeated markup (post cards, comments, draft slides) comes back as
an `html` snippet rendered from the same partial the page uses; simple values come back as plain data.

## Where things live

```
backend/
  templates/
    base.html                   <head> (Inter font, CSS, app.js), blocks: title, head (extra stylesheets), body
    layouts/auth.html           Brand panel (logo links to the landing page) + form panel (login, signup). Block: panel
    layouts/app.html            Sidebar shell for signed in pages (Studio, Publishing, Agent, Replies with the pending
                                review count, Connections, user, log out). Block: content. Pages set `nav_section`
                                ("studio", "publishing", "agent" or "inbox") for the active link
    components/                 Design system partials (see below)
    404.html                    Shown when DEBUG is false
  static/
    css/tokens.css              Design tokens (the only place raw values live)
    css/base.css                Reset and element defaults, .visually-hidden, [hidden]
    css/components.css          Design system classes
    css/pages.css               Layout and feature blocks (auth, shell, provider, account, callback, not-found)
    css/landing.css             Public landing page only, loaded through the `head` block of core/landing.html
    js/landing.js               Landing page highlight boxes (see Landing page), loaded only by core/landing.html
    js/app.js                   Page behaviour: api() and showMessage(), data-api forms, confirm dialogs, busy buttons, load more,
                                photo thumbnails, character counter, post studio draft page
    img/favicon.svg             Brand icon: white geometric "h" plus rose dot on an ink rounded square (same art as brand/icon.svg)
    img/hero-reels.jpg          Hero phone: an earring seller's Instagram reels grid, cropped to the phone frame (corners via `--radius-phone`)
  apps/accounts/
    forms.py                    SignupForm, LoginForm (plus the admin forms)
    views.py, urls.py           login, signup, logout
    templates/accounts/         login.html, signup.html
  apps/integrations/
    views.py, urls.py           connections, connect, OAuth callback, refresh, disconnect
    templates/integrations/     connections.html, provider_card.html, account_row.html,
                                coming_soon_card.html, platform_icon.html, oauth_callback.html
    templatetags/integrations_ui.py  capability_label, status_tone filters
  apps/content/
    forms.py                    ComposeForm (MultipleFileField for carousel uploads)
    views.py, urls.py           studio, dashboard, post detail, comments, compose, publishing, publish-asset
    templates/content/          see DEV-content.md
    templatetags/content_ui.py  metric_display, job_tone filters
  apps/creatives/
    forms.py, views.py, urls.py post studio: create, draft, redo, rewrite, publish, files (see DEV-creatives.md)
    templates/creatives/        new.html, detail.html, draft_card.html
  apps/agent/
    forms.py, views.py, urls.py agent settings, preview, video brain, reply inbox (see DEV-agent.md)
    templates/agent/            account.html, preview.html, inbox.html
    context_processors.py       agent_pending_count for the sidebar
  apps/core/templatetags/hookd_ui.py initials filter (avatar fallback)
  apps/core/templates/core/landing.html  Public landing page (see below)
  apps/core/templates/core/model_figure.html  Inline SVG AI model figure used by the landing page
  apps/core/templates/core/mascot.html  Hooky, the inline SVG mascot (params: uid, size)
```

Settings: `TEMPLATES['DIRS'] = [BASE_DIR / 'templates']`, `STATICFILES_DIRS = [BASE_DIR / 'static']`,
`LOGIN_URL = 'login'`, `LOGIN_REDIRECT_URL = 'studio'`, `MESSAGE_TAGS = {ERROR: 'danger'}`,
`django.contrib.humanize` installed (for `intcomma`).

## Design system

Tokens in `static/css/tokens.css`: brand primary `--color-primary` (#db2777, rose pink), ink `--color-ink`, neutral surfaces,
status colors with `-soft` backgrounds, Inter font, 4px spacing scale (`--space-1..8`), radii `sm/md/lg/full`.

Brand kit in `brand/` at the repo root (not served): `logo.svg` / `logo.png` (ink), `logo-white.svg` / `logo-white.png`
(for dark backgrounds), `icon.svg` / `icon-512.png` (app icon, also the favicon). The wordmark is hand drawn geometric
SVG paths (no font needed). PNGs are exported from the SVGs with macOS `sips -s format png`.
CSS uses tokens only (exception: platform brand colors in `.platform-icon--*`).

| Class / partial | Variants | Notes |
|---|---|---|
| `.btn` | `--primary/--secondary/--ghost/--danger`, `--sm/--md/--lg`, `--block` | Works on `<button>` and `<a>`. app.js adds a spinner and `aria-busy` on submit |
| `components/field.html` | `field=<bound field>` | Label, input, error or `help_text` message with aria ids. Widget `attrs` give `autocomplete`, `placeholder` |
| `components/form_errors.html` | `form=` | First non field error as a danger alert |
| `components/textarea_field.html` | `field=` | Like `field.html` for `Textarea` widgets (`rows`, `placeholder` from widget attrs) |
| `components/checkbox_field.html` | `field=` | `BooleanField` as a `.checkbox` label with help text |
| `components/choice_field.html` | `field=` | Radio `ChoiceField` as a `.choice-group` fieldset |
| `components/select_field.html` | `field=` | `ChoiceField` as a `<select>` styled with `.field__input` |
| `.checkbox` | | Inline checkbox and label |
| `components/messages.html` | | Django messages as `.alert--<level_tag>` |
| `components/replies_badge.html` | `agent_pending_count` | `#nav-replies-count`: the sidebar "Replies" badge, also redrawn by the inbox actions |
| `.card` | `--padded` | `.card__header`, `.card__footer` |
| `.badge` | `--neutral/--success/--warning/--danger/--info` | |
| `.alert` | `--success/--warning/--danger/--info` | `.alert__title`, `.alert__body` |
| `.spinner` | `--sm/--md/--lg` | |
| `components/avatar.html` | `name`, `src`, `size` | Initials when no image or the image fails to load |
| `.page-header` | | `__title`, `__description`, `__actions` |
| `components/brand_logo.html` | `inverse` | Inline SVG "hookd." wordmark (`.logo`), letters use `currentColor`, the dot is `.logo__dot` (`--color-primary`), height `--logo-height`. Same paths as `brand/logo.svg`; change both together |
| `.inline-form` | | `display: contents`, for forms that only wrap buttons |
| `.field__input--textarea`, `--file` | | Textarea and file input variants of `.field__input` |
| `.choice-group` / `.choice` | | Radio options as bordered pills; checked one is highlighted |
| `.tabs` | `__link`, `__link--active` | Horizontal tab links (account switcher) |
| `.metrics` | `__tile`, `__label`, `__value` | Grid of metric tiles (`<dl>`); partial `content/metric_tiles.html` |
| `.section-title`, `.section-header` | | Section heading, and heading row with an action |
| `.hint` | | Small muted text |
| `.back-link` | | "Back to ..." link above a page header |
| `.empty-state` | | Centered empty message with an action |
| `.load-more` | | Centered wrapper for a load more link |

Page blocks for the studio (`pages.css`): `.studio` (`--narrow`, `__account`, `__section`), `.post-grid`, `.post-card`,
`.post-detail`, `.comment-list`, `.comment`, `.compose`, `.job-list`, `.job`. Story pages (agent settings, post studio): `.story` (form column), `.story__actions`,
`.story-step` (numbered card; `__number`, `__body`, `__optional`, `__details`), stacked under 560px. Reply agent:
`.agent-form`, `.agent-limits`, `.agent-facts`, `.agent-reply-text`, `.reply-card`. Post studio: `.dropzone` (`--error`, `__title`, `__hint`,
`__input`), `.photo-preview`, `.tips`, `.look-grid` (`--three`), `.look-choice` (`__title`, `__hint`; card radios), `.progress`,
`.progress-steps` (`__item--active`), `.creative` (`__side`, `__panel`, `__form`, `__actions`, `__inline`), `.ig-preview` (`__head`, `__name`,
`__swipe`, `__caption`), `.slides`, `.slide` (`__media`, `__state--error`, `__count`, `__redo`, `__download`), `.slide-versions`
(`__list`, `__item`; earlier photos in a `<details>`), `.draft-card` (`__media`,
`__working`), `.how-it-works` (`__steps`), `.brain-questions`, `.brain-facts`. `base.css` resets every `fieldset` and `legend`. Layout tokens: `--content-max-width`,
`--content-narrow-width`, `--media-max-height`, `--preview-width`, `--thumb-size`.

`app.js` hooks: `<form data-confirm="...">` asks before submitting. `data-busy-scope` on a container disables all
its buttons while one of its forms submits. Buttons that must stay disabled carry `data-disabled`.
`api(url, {method, body})` fetches a JSON endpoint (FormData bodies, CSRF header, throws with the server's `error`);
`showMessage(text, kind)` shows one alert in `#page-messages` (`layouts/app.html`, hidden while empty).
`<form data-api action="<api url>">` is the way to do an in-page action: the submit posts the form (and a button's
`formaction`) with `api()` instead of loading a page, disables the busy scope's buttons with a spinner on the pressed
one, then applies the answer `{message, message_kind, html, fragments}`: `html` replaces the closest
`data-replace="<selector>"` ancestor (empty html removes it) or fills `data-target="<id>"` (hidden while empty);
`fragments` maps element ids to markup that replaces each element; `message` goes to `showMessage` (`message_kind`
defaults to `success`, errors show as `danger`). Real form posts are left for flows that end on another page: login,
signup, connect (OAuth redirect), compose and the post studio's create, post and delete.
`<button data-load-more="<api url>" data-target="<list id>">` appends the endpoint's `html` to the list and keeps going
with `next` or removes itself; optional `data-more-label` renames it after the first page, `data-empty-text` replaces it
when the first page is empty (details in `DEV-content.md`). `[data-draft]` drives the post studio draft page
(`DEV-creatives.md`).
`<input type="file" data-photo-preview="<list id>">` shows thumbnails of the chosen photos. `<textarea data-char-count="<id>">`
writes "n / max characters" into the element with that id.

## Landing page

`/` renders `core/landing.html` for everyone, so it is the first page people see (logout also returns here), the marketing page with the tagline "Your very own agent to get the world
hookd". Positioned as an AI social media manager for sellers and creators; the page sells the agent first (comment to order on Instagram); the photo studio is a secondary, bonus section near the end.

Look: cream background, rounded pastel cards (butter, lilac, blush, sage, sky, peach, mint), black pill buttons (landing scoped overrides of `.btn`),
the Urbanist display font (loaded in the page's `head` block) with italic `<em>` accents in headings, and Hooky the mascot.
Hooky is `core/mascot.html`: an original inline SVG, a yellow chat bubble with a hook curl on top, cheeks and a smile, wrapped in pink to lilac glow rings
drawn as stacked strokes of one shape (`.mascot`, `__ring--1..5`, `__body`, `__eye`, `__shine`, `__cheek`, `__smile`, `__float`, sizes `--xs` (no rings), `--md`,
default full width). Include it with a unique `uid` per use (the shape's SVG id): `{% include "core/mascot.html" with uid="closing" size="md" %}`.
Hooky appears in the "asks you" learn card and the closing card.

Sections, in order: sticky nav (pill of anchor links Pipeline, How it decides, It learns, Studio, FAQ; log in and get started, or "Open studio" when signed in);
hero with the title, then `.hero-flow` in place of a paragraph (a 5 step stepper with pastel icon circles: buyer comments, checks your brain, replies in their
language, sends the link in DMs, order placed; steps rise in one after another) and `.hero__stage` (`.hero-visual`: a tilted `.hero-phone` image,
`img/hero-reels.jpg`, with a smaller CSS `.reel` card floating over its lower left: pastel gradient, "Agent on" and answered chips, play button,
"Handmade jhumkas, only 20 pieces", views and orders; beside it a `.thread` of four `.bubble`s: buyer comments in Hinglish and Tamil and the agent replies, with `.stage-tag` lead
stages, rising in one after another); an audience strip (`.audience`, pills cycling
pastel tints); the pipeline board (`#pipeline`: five pastel `.board__col` columns New comment, Answered, In DMs, Ordered, Follow up with `.lead` cards, the
same buyer outlined as `.lead--focus` in every column, then three `.perk` cards: any language, any post including ones posted outside Hookd, any hour);
how it decides (`#decide`: `.decide__map` diagram in a white card, comment into voice and brain, out to replies on its own, drafts for you, asks you);
it learns (`#learns`: four pastel `.learn-step` cards: unknown question, `.ask-card` where Hooky asks the seller, the reply saved as a new
`.brain-lines__new` line, the next buyer answered instantly); safe by design (`.safe`, three pastel `.safe-tile`s); the studio bonus (`#studio`: "One product
photo in. A full launch and a trained sales agent out.", four `.bonus-tile`s: upload, AI models, caption and hashtags, agent already knows it); a without /
with Hookd comparison (`#compare`); an FAQ of rounded `<details>` cards (`#faq`, works without JS); a blush closing card with Hooky; a footer with section
links. All CTAs go to `signup` or `login`. Animations (flow steps, hero bubbles, Hooky floating) are off under `prefers-reduced-motion`; the board scrolls
sideways inside itself on narrow screens. The AI model shots are CSS only: `core/model_figure.html` is an inline SVG figure (`.model`, parts `__skin`,
`__hair`, `__top`, `__seam`, `__bottom`) wearing the product, placed in `.mini-look--studio/street/cafe` scenes that vary skin tone and pose through
`--model-skin` and `--model-hair`. Note: the "asks you, then learns" loop is marketing copy; the agent does not yet save inbox replies to the brain
(`DEV-agent.md`).

Highlight boxes (no library): `<main data-highlights>` scrolls normally. The bold part of each section heading (wrapped in
`<span data-highlight>`) is the highlight; the hero has none. Each highlight has `margin-inline-end: var(--space-2)` so its box clears the
words after it. `static/js/landing.js` adds one absolutely positioned
`<svg class="highlight-boxes">` over `.landing` (above the content, so boxes show on filled cards) with two `.highlight-boxes__path` halves per
highlight: a rounded box (padding `--highlight-pad`, corners `--highlight-radius`) that starts at the top middle and draws both sides at once until
they meet at the bottom. A box starts drawing when its text is 70% down the screen and finishes after 20% of a screen of scrolling; headings that
can never get that high (near the bottom of the page) finish at the very bottom. Drawing follows the scroll position (eased in a rAF loop), so
boxes stay drawn below the reader and undraw when scrolling back up. Geometry is built in page coordinates on load, resize, font load and any size
change of `.landing` (`ResizeObserver`, so opening a FAQ item moves the boxes). Off without JavaScript, under `prefers-reduced-motion`, and on
screens narrower than 768px. Anchor links scroll with CSS `scroll-behavior: smooth` (reduced motion: instant) and sections have
`scroll-margin-top` for the sticky nav.

Classes in `landing.css`: `.landing`, `.landing-container`, `.landing-nav`, `.eyebrow`, `.hero`, `.hero-flow` (`__step`, `__step--butter/lilac/blush/sage/win`,
`__icon`), `.hero__stage`, `.hero-visual`, `.hero-phone`, `.reel` (`__media`, `__chip`, `__play`, `__caption`, `__meta`), `.thread`, `.bubble` (`--fan`, `--agent`, `__who`), `.stage-tag`,
`.mascot`, `.section-intro`, `.audience`, `.pipeline`, `.board` (`__col--butter/lilac/blush/sage/sky`, `__head`, `__count`), `.lead` (`--focus`, `__who`,
`__avatar`, `__text`, `__tag--agent/won`), `.perk` (`--peach/mint/lilac`), `.decide`, `.decide-node`, `.decide-out`, `.learn`, `.learn-step`
(`--butter/blush/lilac/sage`), `.pill`, `.ask-card`, `.brain-lines`, `.safe`, `.safe-tile` (`--peach/sky/mint`), `.bonus`, `.bonus-tile`, `.flatlay`,
`.mini-look`, `.mini-caption`, `.mini-tags`, `.mini-bubble`, `.compare`, `.faq`, `.closing` (`__card`, `__mascot`, `__copy`), `.landing-footer`
(`__brand`, `__links`), `.highlight-boxes` (`__path`).
Landing tokens: `--font-display`, `--text-3xl`, `--text-display`, `--leading-display`, `--tracking-display`, `--tracking-eyebrow`, `--space-9/10`,
`--radius-xl`, `--radius-2xl`, `--radius-phone`, `--highlight-color`, `--highlight-width`, `--highlight-pad`, `--highlight-radius`, `--color-ink-soft`, `--color-inverse-muted`, `--transition-reveal`, `--landing-max-width`, the pastels
`--color-cream` and `--color-butter/lilac/blush/sage/peach/sky/mint` (each with `-soft`), the mascot colors `--color-mascot`, `--color-mascot-cheek`,
`--color-mascot-ring-1..5`, and the illustration colors `--color-garment(-shade)`, `--color-model-skin(-alt/-light)`, `--color-model-hair(-alt)`,
`--color-model-bottom`, `--color-scene-studio/street/cafe`.

## Routes (no namespace)

| Method | Path | Name | Auth | Does |
|---|---|---|---|---|
| GET/POST | `/login/` | `login` | guest | `LoginForm`, then `?next=` (only same host, see `_safe_next`) or studio |
| GET/POST | `/signup/` | `signup` | guest | While `SIGNUPS_OPEN` is false (now): `AccessRequestForm`, emails support, redirects to `access-requested`. Otherwise `SignupForm`, creates user, logs in. See `DEV-auth.md` |
| GET | `/signup/requested/` | `access-requested` | | "You are on the list" confirmation |
| POST | `/logout/` | `logout` | | Ends the session, back to the landing page |
| GET | `/health/` | `health` | | JSON health check (`DEV-core.md`) |
| GET | `/` | `home` | none | `core.views.landing`: the landing page, for everyone (signed in users get an "Open studio" button) |
| GET | `/connections/` | `connections` | login | One provider card per provider from `services.connection_overview(user)`, then static `coming_soon_card.html` cards for planned platforms (YouTube: "Coming soon" badge, disabled button, no provider or connect route) |
| POST | `/connections/<platform>/connect/` | `connect-start` | login | Redirects the browser to the platform authorization URL. Used by "Connect", "Connect another" and each account's "Reconnect" button (the callback updates the existing account in place) |
| GET | `/auth/<platform>/callback` | `oauth-callback` | login | Completes the connection, then redirects to connections with a success message. On `error`, missing params or a `HookdError` renders the failure page (400) |
| POST | `/api/connections/accounts/<id>/refresh/` | `api-account-refresh` | login (JSON) | Refreshes the token, redraws the provider card. The "Refresh" button only shows when `provider.supports_token_refresh` (hidden for Instagram) |
| POST | `/api/connections/accounts/<id>/subscribe/` | `api-account-subscribe` | login (JSON) | "Resubscribe" button (shown when `provider.supports_webhooks`): re-runs the webhook subscription with the stored token, no platform login. Redraws the provider card |
| POST | `/api/connections/accounts/<id>/disconnect/` | `api-account-disconnect` | login (JSON) | Deletes the account (confirm dialog), redraws the provider card |
| GET | `/studio/` | `studio` | login | Redirects to the first account's dashboard, or to connections when none is connected |
| GET | `/studio/accounts/<id>/` | `studio-account` | login | Dashboard: insights and 10 recent posts, "Load older posts" calls `api-account-posts`. 404 for other users' accounts |
| GET | `/api/accounts/<id>/posts/?after=` | `api-account-posts` | login (JSON) | Next 10 post cards: `{html, next}` |
| POST | `/api/accounts/<id>/posts/refresh/` | `api-account-posts-refresh` | login (JSON) | Re-syncs recent posts: `{html}` of the recent posts section |
| GET/POST | `/studio/accounts/<id>/compose/` | `compose` | login | `ComposeForm`, publishes, then redirects to publishing |
| GET | `/studio/posts/<id>/` | `post-detail` | login | Post, insights, "Show comments" |
| POST | `/api/posts/<id>/insights/refresh/` | `api-post-insights-refresh` | login (JSON) | Refetches insights: `{html}` of the insights block |
| GET | `/api/posts/<id>/comments/?after=` | `api-post-comments` | login (JSON) | 25 comments: `{html, next, count}` |
| GET | `/studio/publishing/` | `publishing` | login | "Create a post", the user's post studio drafts, then publish jobs of all the user's accounts |
| GET/POST | `/studio/publishing/new/`, `/studio/publishing/drafts/...`, `/studio/publishing/files/...`, `/api/drafts/...`, `/api/slides/...`, `/api/versions/...` | `creative-*`, `api-draft-*`, `api-slide-redo`, `api-version-use` | login | Post studio pages and JSON endpoints, listed in `DEV-creatives.md` |
| POST | `/api/publishing/<id>/check/` | `api-publish-check` | login (JSON) | Advances a processing job: `{html, message, message_kind}` with its row |
| GET | `/publish-media/<token>/` | `publish-asset` | none | Upload file for the platform, only while its job is processing |
| GET, POST | `/agent/...`, `/api/agent/...` | `agent`, `agent-account`, `agent-preview`, `agent-inbox`, `api-agent-settings`, `api-agent-preview`, `api-agent-post-brain`, `api-reply-approve`, `api-reply-reject`, `api-reply-retry` | login | Reply agent pages and JSON actions, listed in `DEV-agent.md` |
| GET, POST | `/webhooks/<platform>/` | `webhook` | none, CSRF exempt | Platform webhooks: GET handshake echoes the challenge, POST verifies the signature and stores comments. 403 on bad token or signature, 404 unknown platform. See `DEV-comments.md` |

The callback path has no trailing slash because it must match `INSTAGRAM_REDIRECT_URI`. If the session was lost,
`login_required` sends the creator to `/login/?next=<callback with query>` and login returns them there.

Errors: page views catch `HookdError` and show `exc.message` as a Django message or on the callback page; JSON views
answer `{"error"}` and `app.js` shows it with `showMessage`.

## Tests

`apps/accounts/tests.py` `AuthPageTests`: signup, field errors, bad password, safe `next`, logout.
`apps/integrations/tests.py` `ConnectionPageTests`: see `DEV-integrations.md`.
`apps/content/tests.py`: see `DEV-content.md`.
`apps/agent/tests.py`: see `DEV-agent.md`.
