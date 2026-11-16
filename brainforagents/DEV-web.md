# DEV: Web UI (Django templates)

The UI is server rendered with Django templates and session auth. No JavaScript framework, no build step.
Views are thin: validate input, call `services.py`, render a template or redirect.

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
    js/app.js                   Progressive enhancement only (confirm dialogs, busy buttons, load more links)
    img/favicon.svg
  apps/accounts/
    forms.py                    SignupForm, LoginForm (plus the admin forms)
    views.py, urls.py           login, signup, logout
    templates/accounts/         login.html, signup.html
  apps/integrations/
    views.py, urls.py           connections, connect, OAuth callback, refresh, disconnect
    templates/integrations/     connections.html, provider_card.html, account_row.html,
                                platform_icon.html, oauth_callback.html
    templatetags/integrations_ui.py  capability_label, status_tone filters
  apps/content/
    forms.py                    ComposeForm (MultipleFileField for carousel uploads)
    views.py, urls.py           studio, dashboard, post detail, comments, compose, publishing, publish-asset
    templates/content/          see DEV-content.md
    templatetags/content_ui.py  metric_display, job_tone filters
  apps/agent/
    forms.py, views.py, urls.py agent settings, rules, preview, video brain, reply inbox (see DEV-agent.md)
    templates/agent/            account.html, rule_item.html, rule_form.html, preview.html, inbox.html
    context_processors.py       agent_pending_count for the sidebar
  apps/core/templatetags/hookd_ui.py initials filter (avatar fallback)
  apps/core/templates/core/landing.html  Public landing page (see below)
```

Settings: `TEMPLATES['DIRS'] = [BASE_DIR / 'templates']`, `STATICFILES_DIRS = [BASE_DIR / 'static']`,
`LOGIN_URL = 'login'`, `LOGIN_REDIRECT_URL = 'studio'`, `MESSAGE_TAGS = {ERROR: 'danger'}`,
`django.contrib.humanize` installed (for `intcomma`).

## Design system

Tokens in `static/css/tokens.css`: brand primary `--color-primary` (#ff4f2b), ink `--color-ink`, neutral surfaces,
status colors with `-soft` backgrounds, Inter font, 4px spacing scale (`--space-1..8`), radii `sm/md/lg/full`.
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
| `.card` | `--padded` | `.card__header`, `.card__footer` |
| `.badge` | `--neutral/--success/--warning/--danger/--info` | |
| `.alert` | `--success/--warning/--danger/--info` | `.alert__title`, `.alert__body` |
| `.spinner` | `--sm/--md/--lg` | |
| `components/avatar.html` | `name`, `src`, `size` | Initials when no image or the image fails to load |
| `.page-header` | | `__title`, `__description`, `__actions` |
| `components/brand_logo.html` | `inverse` | |
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
`.post-detail`, `.comment-list`, `.comment`, `.compose`, `.job-list`, `.job`. Reply agent: `.agent-layout`, `.agent-side`,
`.agent-form`, `.agent-limits`, `.agent-facts`, `.agent-reply-text`, `.rule-list`, `.rule`, `.reply-card`. Layout tokens: `--content-max-width`,
`--content-narrow-width`, `--media-max-height`.

`app.js` hooks: `<form data-confirm="...">` asks before submitting. `data-busy-scope` on a container disables all
its buttons while one of its forms submits. Buttons that must stay disabled carry `data-disabled`.
`<a data-load-more="<list id>">` appends the next page's items in place (details in `DEV-content.md`).

## Landing page

`/` renders `core/landing.html` for everyone, so it is the first page people see (logout also returns here), the marketing page with the tagline "Your very own agent to get the world
hookd". Sections: sticky nav (anchor links; log in and get started, or "Open studio" when signed in), hero with a CSS only mock (edited reel, agent replies
in Hinglish and Tamil, a sale toast; messages rise in with a staggered animation, off under `prefers-reduced-motion`),
the four step flow (`#flow`: upload, Hookd edits, agent replies, sale), the agent and video brain card (`#agent`),
languages (`#languages`), a dark closing CTA and a footer. All CTAs go to `signup` or `login`. Classes in
`landing.css`: `.landing`, `.landing-container`, `.landing-nav`, `.eyebrow`, `.hero`, `.reel`, `.thread`,
`.sale-toast`, `.section-intro`, `.flow`, `.flow-step`, `.feature`, `.brain-card`, `.checklist`, `.lang-cloud`,
`.closing`, `.landing-footer`. Landing tokens: `--text-3xl`, `--text-display`, `--leading-display`,
`--tracking-display`, `--tracking-eyebrow`, `--space-9/10`, `--radius-xl`, `--shadow-lg`, `--color-ink-soft`,
`--color-glow`, `--color-glow-cool`, `--color-inverse-muted/subtle`, `--transition-reveal`, `--landing-max-width`.

## Routes (no namespace)

| Method | Path | Name | Auth | Does |
|---|---|---|---|---|
| GET/POST | `/login/` | `login` | guest | `LoginForm`, then `?next=` (only same host, see `_safe_next`) or studio |
| GET/POST | `/signup/` | `signup` | guest | While `SIGNUPS_OPEN` is false (now): `AccessRequestForm`, emails support, redirects to `access-requested`. Otherwise `SignupForm`, creates user, logs in. See `DEV-auth.md` |
| GET | `/signup/requested/` | `access-requested` | | "You are on the list" confirmation |
| POST | `/logout/` | `logout` | | Ends the session, back to the landing page |
| GET | `/health/` | `health` | | JSON health check (`DEV-core.md`) |
| GET | `/` | `home` | none | `core.views.landing`: the landing page, for everyone (signed in users get an "Open studio" button) |
| GET | `/connections/` | `connections` | login | One provider card per provider from `services.connection_overview(user)` |
| POST | `/connections/<platform>/connect/` | `connect-start` | login | Redirects the browser to the platform authorization URL. Used by "Connect", "Connect another" and each account's "Reconnect" button (the callback updates the existing account in place) |
| GET | `/auth/<platform>/callback` | `oauth-callback` | login | Completes the connection, then redirects to connections with a success message. On `error`, missing params or a `HookdError` renders the failure page (400) |
| POST | `/connections/accounts/<id>/refresh/` | `account-refresh` | login | Refreshes the token, message, back to connections. The "Refresh" button only shows when `provider.supports_token_refresh` (hidden for Instagram) |
| POST | `/connections/accounts/<id>/subscribe/` | `account-subscribe` | login | "Resubscribe" button (shown when `provider.supports_webhooks`): re-runs the webhook subscription with the stored token, no platform login. Message, back to connections |
| POST | `/connections/accounts/<id>/disconnect/` | `account-disconnect` | login | Deletes the account (confirm dialog), message, back to connections |
| GET | `/studio/` | `studio` | login | Redirects to the first account's dashboard, or to connections when none is connected |
| GET | `/studio/accounts/<id>/` | `studio-account` | login | Dashboard: insights and 10 recent posts; `?after=<cursor>` shows the next 10. 404 for other users' accounts |
| POST | `/studio/accounts/<id>/refresh/` | `studio-refresh` | login | Re-syncs recent posts, back to the dashboard |
| GET/POST | `/studio/accounts/<id>/compose/` | `compose` | login | `ComposeForm`, publishes, then redirects to publishing |
| GET | `/studio/posts/<id>/` | `post-detail` | login | Post, insights, "Show comments" |
| POST | `/studio/posts/<id>/insights/refresh/` | `post-insights-refresh` | login | Refetches insights |
| GET | `/studio/posts/<id>/comments/` | `post-comments` | login | 25 comments; `?after=<cursor>` for the next page |
| GET | `/studio/publishing/` | `publishing` | login | Publish jobs of all the user's accounts |
| POST | `/studio/publishing/<id>/check/` | `publish-check` | login | Advances a processing job |
| GET | `/publish-media/<token>/` | `publish-asset` | none | Upload file for the platform, only while its job is processing |
| GET/POST | `/agent/...` | `agent`, `agent-account`, `agent-starter-rules`, `agent-preview`, `agent-rule-new`, `agent-rule-edit`, `agent-rule-delete`, `agent-post-brain`, `agent-inbox`, `agent-reply-approve`, `agent-reply-reject`, `agent-reply-retry` | login | Reply agent pages, listed in `DEV-agent.md` |
| GET, POST | `/webhooks/<platform>/` | `webhook` | none, CSRF exempt | Platform webhooks: GET handshake echoes the challenge, POST verifies the signature and stores comments. 403 on bad token or signature, 404 unknown platform. See `DEV-comments.md` |

The callback path has no trailing slash because it must match `INSTAGRAM_REDIRECT_URI`. If the session was lost,
`login_required` sends the creator to `/login/?next=<callback with query>` and login returns them there.

Errors: page views catch `HookdError` and show `exc.message` as a Django message or on the callback page.

## Tests

`apps/accounts/tests.py` `AuthPageTests`: signup, field errors, bad password, safe `next`, logout.
`apps/integrations/tests.py` `ConnectionPageTests`: see `DEV-integrations.md`.
`apps/content/tests.py`: see `DEV-content.md`.
`apps/agent/tests.py`: see `DEV-agent.md`.
