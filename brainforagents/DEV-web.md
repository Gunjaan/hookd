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
    base.html                   <head> (Inter and Urbanist fonts, CSS, app.js, SEO and link preview tags, see below),
                                blocks: title, robots (default "index, follow"), head (extra stylesheets), body
    layouts/auth.html           Brand panel (logo links to the landing page) + form panel (login, signup). Block: panel
    layouts/app.html            Shell for signed in pages: a sticky white side panel (`.app-side`) with icon links Profile,
                                Create, Replies (pending review count), Tickets, Agent (Hooky as its icon), Connections at the
                                bottom, then the user and a log out icon button. Under 768px the panel is a top bar (logo,
                                avatar, log out) and the same links become a fixed bottom tab bar, like the Instagram app.
                                Block: content. Pages set `nav_section` ("studio", "publishing", "agent", "inbox" or "tickets").
                                Also holds the shared `<dialog id="modal">` (`.modal`) that `data-modal` links fill
    components/                 Design system partials (see below), including icon.html: line icons (grid, plus, chat,
                                heart, reel, carousel, refresh, upload, external, sparkle, reply, plug, drafts, back, logout)
    404.html                    Shown when DEBUG is false
  static/
    css/tokens.css              Design tokens (the only place raw values live)
    css/base.css                Reset and element defaults, .visually-hidden, [hidden]
    css/components.css          Design system classes
    css/pages.css               Layout and feature blocks (auth, shell, provider, account, callback, not-found)
    css/landing.css             Public landing page only, loaded through the `head` block of core/landing.html
    js/landing.js               Landing page highlight boxes and floating video dock (see Landing page), loaded only by core/landing.html
    js/app.js                   Page behaviour: api() and showMessage(), data-api forms, confirm dialogs, busy buttons, load more,
                                photo thumbnails, character counter, post studio draft page
    img/favicon.svg             Brand icon: white geometric "h" plus rose dot on an ink rounded square (same art as brand/icon.svg)
    img/og-image.png            1200x630 link preview card (logo, hero headline, three hero flow pills on cream)
    img/apple-touch-icon.png    180x180 PNG of the favicon art for iOS home screens
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
    templates/agent/            account.html, status_bar.html, inbox.html
    context_processors.py       agent_pending_count for the side panel badge
  apps/core/templatetags/hookd_ui.py initials filter (avatar fallback)
  apps/core/templates/core/landing.html  Public landing page (see below)
  apps/core/templates/core/model_figure.html  Inline SVG AI model figure used by the landing page
  apps/core/templates/core/mascot.html  Hooky, the inline SVG mascot (params: uid, size)
```

Settings: `TEMPLATES['DIRS'] = [BASE_DIR / 'templates']`, `STATICFILES_DIRS = [BASE_DIR / 'static']`,
`STATIC_ROOT = BASE_DIR / 'staticfiles'` (hashed manifest storage in production, see `DEV-infrastructure.md`),
`LOGIN_URL = 'login'`, `LOGIN_REDIRECT_URL = 'studio'`, `MESSAGE_TAGS = {ERROR: 'danger'}`,
`django.contrib.humanize` installed (for `intcomma`).

## SEO and link previews

`base.html` gives every page a meta description, `robots`, a canonical URL, the SVG favicon plus `apple-touch-icon`,
Open Graph tags (`og:type`, `og:site_name`, `og:locale` en_IN, `og:url`, `og:title`, `og:description`, `og:image` with
type, size and alt) and Twitter `summary_large_image` tags. These are what WhatsApp, Instagram, Facebook, LinkedIn,
Slack, X and iMessage read when someone shares a hookd.world link. Copy is site wide (only the landing page is public).
Absolute URLs come from `request.scheme` and `request.get_host`, so they are `https://hookd.world/...` in production
(Caddy sends `X-Forwarded-Proto`, read through `SECURE_PROXY_SSL_HEADER`). `og:image` points at the hashed static file,
so changing `og-image.png` gives a new URL and previews pick it up. Check with https://metatags.io or the Facebook
Sharing Debugger (which also clears Meta's cache). `layouts/app.html` and `integrations/oauth_callback.html` set
`robots` to "noindex, nofollow".

## Design system

The signed in app shares the landing page look: cream background (`--color-bg` is `--color-cream`), warm neutral surfaces
and borders, white or pastel cards with `--radius-xl`, black pill buttons, Urbanist (`--font-display`) for headings, buttons,
nav, tabs and numbers with Inter for body text, italic rose `<em>` accents in titles, and Hooky the mascot.
Tokens in `static/css/tokens.css`: rose `--color-primary` (#db2777) for accents, links, focus and badges, ink `--color-ink`
for primary buttons and active states, the landing pastels, status colors with `-soft` backgrounds, 4px spacing scale
(`--space-1..10`), radii `sm/md/lg/xl/2xl/full`.

Brand kit in `brand/` at the repo root (not served): `logo.svg` / `logo.png` (ink), `logo-white.svg` / `logo-white.png`
(for dark backgrounds), `icon.svg` / `icon-512.png` (app icon, also the favicon). The wordmark is hand drawn geometric
SVG paths (no font needed). PNGs are exported from the SVGs with macOS `sips -s format png`.
CSS uses tokens only (exception: platform brand colors in `.platform-icon--*`).

| Class / partial | Variants | Notes |
|---|---|---|
| `.btn` | `--primary` (ink pill) `/--secondary` (ink outline) `/--ghost/--danger`, `--sm/--md/--lg`, `--block`, `--icon` (round, icon only, needs `aria-label`) | Works on `<button>` and `<a>`. app.js adds a spinner and `aria-busy` on submit |
| `components/field.html` | `field=<bound field>` | Label, input, error or `help_text` message with aria ids. Widget `attrs` give `autocomplete`, `placeholder` |
| `components/form_errors.html` | `form=` | First non field error as a danger alert |
| `components/textarea_field.html` | `field=` | Like `field.html` for `Textarea` widgets (`rows`, `placeholder` from widget attrs) |
| `components/checkbox_field.html` | `field=`, `switch=True` | `BooleanField` as a `.checkbox` label with help text; `switch` draws an on or off switch (`.checkbox--switch`, `role="switch"`) |
| `components/choice_field.html` | `field=` | Radio `ChoiceField` as a `.choice-group` fieldset |
| `components/select_field.html` | `field=` | `ChoiceField` as a `<select>` styled with `.field__input` |
| `.checkbox` | | Inline checkbox and label |
| `components/messages.html` | | Django messages as `.alert--<level_tag>` |
| `components/replies_badge.html` | `agent_pending_count` | `#nav-replies-count`: the side panel "Replies" badge (replies and DMs to review plus Hooky's open questions), also redrawn by the inbox actions |
| `components/icon.html` | `name`, `size` | Inline SVG line icon (`.icon`), `currentColor` |
| `core/mascot.html` | `uid`, `size` | Hooky (`.mascot`, styles in `components.css`), used by the app too: nav, profile avatar badge, highlights, panels, empty states |
| `.bubble` | `--fan`, `--agent`, `__who` | Chat bubbles: commenter (white) and agent reply (ink). Landing thread, reply inbox, preview |
| `.modal` | `__close`, `__messages`, `__body`, `__loading` | Shared `<dialog>` in `layouts/app.html`, width `--modal-width` |
| `.carousel` | `__track`, `__nav`, `__btn--prev/next`, `__dots`, `__dot` | Swipeable row (`data-carousel`) with optional arrows over a 4:5 photo and dots; app.js keeps them in step. Used by the draft preview and the photo viewer (`.photo-viewer`, `.modal--photo`, width `--photo-viewer-width`) |
| `.chips` / `.chip` | | Small pill buttons that add a value (voice styles) |
| `.panel` | `--butter/lilac/blush/sage/sky` | Pastel rounded block with `__head`, `__mascot`, `__lede`; inputs and tiles stay white on it |
| `.card` | `--padded` | `.card__header`, `.card__footer` |
| `.badge` | `--neutral/--success/--warning/--danger/--info` | |
| `.alert` | `--success/--warning/--danger/--info` | `.alert__title`, `.alert__body` |
| `.spinner` | `--sm/--md/--lg` | |
| `components/avatar.html` | `name`, `src`, `size` | Initials when no image or the image fails to load |
| `.page-header` | | `__title` (Urbanist, an `<em>` word is the rose italic accent), `__description`, `__actions` |
| `components/brand_logo.html` | `inverse` | Inline SVG "hookd." wordmark (`.logo`), letters use `currentColor`, the dot is `.logo__dot` (`--color-primary`), height `--logo-height`. Same paths as `brand/logo.svg`; change both together |
| `.inline-form` | | `display: contents`, for forms that only wrap buttons |
| `.field__input--textarea`, `--file` | | Textarea and file input variants of `.field__input` |
| `.choice-group` / `.choice` | | Radio options as bordered pills; checked one is highlighted |
| `.tabs` | `__link`, `__link--active`, `__link--empty` (dimmed, count 0), `__count` | White pill switcher like the landing nav, active link in ink (account switcher, inbox status) |
| `.metrics` | `__tile`, `__label`, `__value`, `--inline` | Grid of white metric tiles (`<dl>`), or one row of numbers; partial `content/metric_tiles.html` (`inline=True`) |
| `.section-title`, `.section-header` | | Section heading, and heading row with an action |
| `.hint` | | Small muted text |
| `.back-link` | | "Back to ..." link above a page header |
| `.empty-state` | `__mascot`, `__title` | Centered empty message, usually with Hooky and an action |
| `.load-more` | | Centered wrapper for a load more link |

App shell (`pages.css`): `.app`, `.app-side` (`__logo`, `__nav`, `__link`, `__link--active`, `__link--secondary`, `__user`,
`__user-info`, `__user-name`, `__user-email`), `.app-main` (`__messages`).
Page blocks for the studio: `.studio` (centered column, `--narrow`, `--medium`, `__section`), the profile (`.profile`: `__avatar`,
`__avatar--live` (Hooky's ring colors around the avatar while the agent is on), `__hooky`, `__body`, `__top`, `__username`,
`__actions`, `__stats`, `__bio`, `__name`, `__status(--on)`, `__switcher`; on phones a grid with the avatar beside the name and
numbers like the Instagram app), `.highlights` / `.highlight` (`--butter/lilac/blush/sage/sky`, `__circle`, `__count`,
`__label`; story highlight style shortcuts), `.profile-insights`, `.profile-tabs`, `.post-grid` (3 columns), `.post-tile`
(`__link`, `__fallback` (pastel caption square when there is no image or it fails), `__type`, `__overlay` (likes and comments
on hover), `__status`, `__working`), the post page (`.post-view`: `__media`, `__side`, `__head`, `__name`, `__open`,
`__thread`, `__caption`, `__more`, `__foot`; `.post-extras`), the post modal (`.post-modal`: `__media`, `__side`, `__body`,
`__caption`, `__meta`, `__brain`, `__foot`; both columns are capped at the modal height so the photo stays centred and
only `__body` scrolls, and the header badges wrap), `.comment-list`, `.comment` (`__body`, `__author`, `__text`,
`__time`), `.compose`, `.job-list`, `.job`, the create page (`.create-hero`: `__copy`, `__lede`, `__actions`, `__steps`;
`.create-step--butter/lilac/sage`, `__icon`). Story pages (post studio): `.story` (form column),
`.story__actions`, `.story-step` (numbered pastel card, `--butter/lilac/blush/sage`; `__number`, `__body`, `__optional`),
stacked under 560px. Reply agent: `.agent-status` (`--on`, `__mascot`, `__text`, `__title`, `__account`, `__meta`, `__paused`,
`__pending`, `__switch`), `.agent-layout`, `.agent-settings`, `.agent-card` (`__lede`), `.save-bar` (`__text`), `.agent-try`
(`__form`, `__result`), `.agent-safety` (`__more`), `.agent-form`, `.agent-limits`, `.agent-preview` (`__outcome`, `__reply`), `.reply-list`, `.reply-card` (`__post`, `__thumb`, `__why`, `__why-icon`,
`__note`, `__retry`), `.reply-row` (`--you` indented, `__text`, `__meta`), `.reply-composer` (`__label`, `__input`, `__actions`),
`.run-trail` (a `<details>` of the agent's tool calls: `__steps`, `__step`, `--error`, `__arg`, `__result`), `.ticket-form`. Post studio: `.dropzone` (`--error`, `__title`, `__hint`,
`__input`), `.photo-preview`, `.tips`, `.look-grid` (`--three`), `.look-choice` (`__title`, `__hint`; card radios), `.progress`,
`.progress-steps` (`__item--active`), `.creative` (`__side`, `__panel`, `__form`, `__actions`, `__inline`), `.ig-preview` (`__head`, `__name`,
`__caption`), `.slide` (`__media`, `__state--error`, `__count`, `__redo`, `__download`), `.draft-story` (`__step`, `__step--done/active/todo`, `__marker`, `__marker--photo`, `__title`, `__detail`, `__view`), `.slide-versions`
(`__list`, `__item`; earlier photos in a `<details>`), `.brain-questions`, `.brain-facts`. Learned brain: `.reply-card__learned` (`-title`; inbox card list of what a reply taught), `.agent-column` (agent page left column: settings form plus the
learned card), `.learned-block` (`__title`; the list under a Video brain form), `.learned-facts`, `.learned-fact`
(`__text`, `__meta`, `__forget`). `base.css` resets every
`fieldset` and `legend`. Layout tokens: `--sidebar-width`, `--nav-height`, `--tabbar-height`, `--content-max-width`,
`--content-narrow-width`, `--content-medium-width`, `--modal-width`, `--photo-viewer-width`, `--media-max-height`, `--preview-width`, `--thumb-size`, `--profile-avatar(-sm)`,
`--highlight-size(-sm)`, `--overlay-media`.

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
`data-include="<form id>"` on a data-api form also sends that form's fields it does not have itself.
`<form data-unsaved="<bar id>">` shows the bar after any edit and hides it on a successful save or a reset.
`<a href="<page>" data-modal="<api url>">` opens the shared modal with the endpoint's `html` instead of leaving the
page (Ctrl, Cmd or Shift click still opens the page; close button, backdrop click or Esc closes it; `showMessage` writes
into the open modal). `<a href="<first photo>" data-modal-images="<url> <url>" data-modal-label="...">` shows the photos in the modal as a
carousel. `<div data-carousel>`: `[data-carousel-step="-1|1"]` buttons scroll one item, `[data-carousel-dot="<n>"]`
jumps, both follow swipes (disabled at the ends, `aria-current` dot). `<input data-autosubmit>` submits its form on change. `<textarea data-send-shortcut>`: Ctrl or Cmd + Enter submits its form. `<button data-append-to="<field id>" data-text="...">` adds the
text to the end of that field once. `<input type="file" data-photo-preview="<list id>">` shows thumbnails of the chosen photos. `<textarea data-char-count="<id>">`
writes "n / max characters" into the element with that id.

## Landing page

`/` renders `core/landing.html` for everyone, so it is the first page people see (logout also returns here), the marketing page with the tagline "Your very own agent to get the world
hookd". Positioned as an AI social media manager for sellers and creators; the page sells the agent first (comment to order on Instagram); the photo studio is a secondary, bonus section near the end.

Look: cream background, rounded pastel cards (butter, lilac, blush, sage, sky, peach, mint), black pill buttons (the shared `.btn`),
the Urbanist display font (loaded by `base.html`) with italic `<em>` accents in headings, and Hooky the mascot.
Hooky is `core/mascot.html` (styles in `components.css`, shared with the app): an original inline SVG, a yellow chat bubble with a hook curl on top, cheeks and a smile, wrapped in pink to lilac glow rings
drawn as stacked strokes of one shape (`.mascot`, `__ring--1..5`, `__body`, `__eye`, `__shine`, `__cheek`, `__smile`, `__float`, sizes `--xs` (no rings), `--md`,
default full width). Include it with a unique `uid` per use (the shape's SVG id): `{% include "core/mascot.html" with uid="closing" size="md" %}`.
Hooky appears in the "asks you" learn card and the closing card.

Sections, in order: sticky nav (pill of anchor links How it works, How it decides, It learns, Try it, FAQ, Contact; log in and get started, or "Open studio" when signed in);
hero with the title, then `.hero-flow` in place of a paragraph (a 5 step stepper with pastel icon circles: buyer comments, checks your brain, replies in their
language, sends the link in DMs, order placed; steps rise in one after another), CTAs (signup and "Try it on Instagram" to `#try`) and `.hero__stage` (`.hero-visual`: a tilted `.hero-phone` image,
`img/hero-reels.jpg`, with a smaller CSS `.reel` card floating over its lower left: pastel gradient, "Agent on" and answered chips, play button,
"Handmade jhumkas, only 20 pieces", views and orders; beside it a `.thread` of four `.bubble`s: buyer comments in Hinglish and Tamil and the agent replies, with `.stage-tag` lead
stages, rising in one after another); an audience strip (`.audience`, pills cycling
pastel tints); one comment start to finish (`#pipeline`: five pastel `.journey__step` cards from her comment to the follow up, then a `.funnel` row);
how it decides (`#decide`: a white `.decide__card` with the agent `.loop` (comment or DM arrives, Claude picks the next tool, our code checks the call, the
result goes back to Claude, joined by `.decide__arrow`s, vertical under 1024px), a `.loop__repeat` line (8 steps a turn), and `.tools`: four
`.tools__group`s listing Hooky's 10 tools as `.tool-chip` `<code>` chips; below the card three `.decide-out` outcomes (replies on its own, holds it in
approval mode, asks you) and a `.decide__note` that every tool call shows in the inbox);
it learns (`#learns`: four pastel `.learn-step` cards: unknown question, `.ask-card` where Hooky asks the seller, the reply saved as a new
`.brain-lines__new` line, the next buyer answered instantly); try it live (`#try`: `.try__steps` card with three `.try-step`s to comment on or DM the
demo shop @while.wanderingg on Instagram, and a blush `.try__side` card linking to `#contact` to see Hooky's side); safe by design (`.safe`, "The guardrails
are code, not a prompt": six pastel `.safe-tile`s with the real limits from `DEV-agent.md`: number guard, pacing, Meta DM windows, step caps, question
caps, starts off and approval mode); the studio bonus (`#studio`: "One product
photo in. A full launch and a trained sales agent out.", four `.bonus-tile`s: upload, AI models, caption and hashtags, agent already knows it); a without /
with Hookd comparison (`#compare`); an FAQ of rounded `<details>` cards (`#faq`, works without JS); a blush closing card with Hooky; the contact
section (`#contact`: `.contact__card` with copy and a `data-api` form posting `ContactForm` to `api-contact` with `data-target="contact-box"`, so the
thank you note from `core/contact_sent.html` replaces the form; errors go to the card's own `#page-messages`; see `DEV-core.md`); a footer with "Built with
Claude. Hosted on GoDaddy", section links, GitHub and log in. Floating over every section, bottom right: the video dock (`.video-dock`, `data-video-dock`), an
ink "Watch the demo" pill that opens a white card with two tabs, "Product demo" (YouTube `7HOha1_jlVM`) and "Connect Instagram" (`lWhaOPulgSA`);
`landing.js` creates the `youtube-nocookie.com` embed iframe (autoplay, `referrerpolicy="strict-origin-when-cross-origin"` because YouTube rejects embeds without a referrer with error 153 and Django's default policy is `same-origin`) only when the card opens or a tab is picked and removes it on close or Escape,
so nothing loads from YouTube until a visitor asks. To change a video, edit the tab's `data-video-id` and `data-video-title`. Account CTAs go to `signup` or `login`. Animations (flow steps, hero bubbles, Hooky floating) are off under `prefers-reduced-motion`; the board scrolls
sideways inside itself on narrow screens. The AI model shots are CSS only: `core/model_figure.html` is an inline SVG figure (`.model`, parts `__skin`,
`__hair`, `__top`, `__seam`, `__bottom`) wearing the product, placed in `.mini-look--studio/street/cafe` scenes that vary skin tone and pose through
`--model-skin` and `--model-hair`.

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
`__icon`), `.hero__stage`, `.hero-visual`, `.hero-phone`, `.reel` (`__media`, `__chip`, `__play`, `__caption`, `__meta`), `.thread` (animates the shared `.bubble`s), `.stage-tag`,
`.section-intro`, `.audience`, `.pipeline`, `.journey`, `.funnel`, `.decide` (`__card`, `__arrow`, `__outcomes`, `__note`), `.loop` (`__step--claude/code/result`,
`__repeat`), `.tools` (`__group`, `__label`, `__chips`), `.tool-chip`, `.decide-out`, `.learn`, `.learn-step`
(`--butter/blush/lilac/sage`), `.pill`, `.ask-card`, `.brain-lines`, `.try` (`__grid`, `__steps`, `__side`, `__side-title`), `.try-step` (`__num`, `__title`,
`__body`), `.safe`, `.safe-tile` (`--peach/sky/mint/butter/lilac/sage`), `.bonus`, `.bonus-tile`, `.flatlay`,
`.mini-look`, `.mini-caption`, `.mini-tags`, `.mini-bubble`, `.compare`, `.faq`, `.closing` (`__card`, `__mascot`, `__copy`), `.contact` (`__card`, `__copy`,
`__panel`, `__form`, `__sent`, `__sent-title`), `.video-dock` (`__toggle`, `__play`, `__panel`, `__head`, `__title`, `__close`, `__tabs`, `__tab`, `__player`), `.landing-footer`
(`__brand`, `__links`), `.highlight-boxes` (`__path`).
Landing tokens: `--font-display`, `--font-mono` (tool chips), `--text-3xl`, `--text-display`, `--leading-display`, `--tracking-display`, `--tracking-eyebrow`, `--space-9/10`,
`--radius-xl`, `--radius-2xl`, `--radius-phone`, `--highlight-color`, `--highlight-width`, `--highlight-pad`, `--highlight-radius`, `--color-ink-soft`, `--color-inverse-muted`, `--transition-reveal`, `--landing-max-width`, `--video-dock-width`, `--shadow-lg`, the pastels
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
| GET | `/.well-known/agent-card.json` | `agent_card` | | Public GoDaddy ANS agent card for Hooky (`DEV-agent.md`) |
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
| GET | `/api/posts/<id>/preview/` | `api-post-preview` | login (JSON) | The post and its Video brain for the modal: `{html}` |
| GET | `/studio/publishing/` | `publishing` | login | "Create a post", the user's post studio drafts, then publish jobs of all the user's accounts |
| GET/POST | `/studio/publishing/new/`, `/studio/publishing/drafts/...`, `/studio/publishing/files/...`, `/api/drafts/...`, `/api/slides/...`, `/api/versions/...` | `creative-*`, `api-draft-*`, `api-slide-redo`, `api-version-use` | login | Post studio pages and JSON endpoints, listed in `DEV-creatives.md` |
| POST | `/api/publishing/<id>/check/` | `api-publish-check` | login (JSON) | Advances a processing job: `{html, message, message_kind}` with its row |
| GET | `/publish-media/<token>/` | `publish-asset` | none | Upload file for the platform, only while its job is processing |
| GET, POST | `/agent/...`, `/api/agent/...` | `agent`, `agent-account`, `agent-inbox`, `agent-tickets`, `api-agent-settings`, `api-agent-enabled`, `api-agent-preview`, `api-agent-post-brain`, `api-agent-fact-forget`, `api-reply-approve`, `api-reply-reject`, `api-reply-retry`, `api-dm-approve`, `api-dm-reject`, `api-question-answer`, `api-question-dismiss`, `api-ticket-update` | login | Reply agent pages and JSON actions, listed in `DEV-agent.md` |
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
