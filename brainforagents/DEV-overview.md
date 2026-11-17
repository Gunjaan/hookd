# DEV: Overview

Hookd gives every video its own AI community manager that replies in the creator's voice and the
commenter's language. Product plan: `hookd-plan.md` at the repo root.

## Current scope

Working today: **public landing page at `/`, login (self serve signup is closed: `/signup/` emails an access request
to support, see `DEV-auth.md`), connect an Instagram professional account, receive its new comments by webhook,
store its DMs, story replies and story mentions when the `messages` webhook is on (no auto DMs yet),
the creator studio (recent posts, post and account insights, paged comments), publishing images, reels, carousels and stories, the post studio (product photos in, AI made post photos with an editable
caption and hashtags out, plus questions that teach the reply agent about the product, Gemini for now), and
the comment reply agent (account and video brains that also fill themselves from captions and the replies the creator writes, sends on its own when they hold the answer, review inbox for the rest, rate limited sending) writing replies with a low cost Gemini
model.**
Not built yet: YouTube, sending DMs, private replies and auto replies to DMs and story replies, the Claude engines
(reply agent and post studio copy), workflows, scheduled posts.

## Feature index

| File | Covers |
|---|---|
| `DEV-infrastructure.md` | deploy.py and Docker, SQLite, cache, env vars, cron, Celery, running and testing |
| `DEV-auth.md` | `apps.accounts`: custom User, session login, signup, logout |
| `DEV-core.md` | `apps.core`: base model, encrypted field, `HookdError`, health check |
| `DEV-integrations.md` | `apps.integrations`: provider interface, SocialAccount, connect flow, token refresh |
| `DEV-instagram.md` | Instagram provider: Unified Login (Facebook Login for Business), token lifecycle, Meta app setup |
| `DEV-comments.md` | `apps.comments`: Comment model, webhook endpoint, how comments arrive |
| `DEV-messaging.md` | `apps.messaging`: DMs, story replies and story mentions from the `messages` webhook, 24 hour reply window (groundwork for auto DMs) |
| `DEV-agent.md` | `apps.agent`: account and video brains, learned brain (captions and inbox replies), confidence based auto send, engine interface (Gemini, simulated fallback), review inbox, Meta safe sending |
| `DEV-content.md` | `apps.content`: studio dashboard, lazy loading, Post, insights, comments paging, publishing |
| `DEV-creatives.md` | `apps.creatives`: post studio, product photos to ready to post drafts (Gemini engines, prompts, token rules) |
| `DEV-web.md` | Server rendered UI: Django templates, design system CSS, page routes, session auth |

## Repository layout

```
hookd/
  AGENTS.md, CLAUDE.md        Agent rules (CLAUDE.md imports AGENTS.md)
  README.md                   Human quick start
  deploy.py                   Docker helper: build, run, deploy, install-cron
  Dockerfile                  App image (Python only), code is mounted
  .env.example                Every env var, documented
  brainforagents/             These DEV docs
  backend/                    Django project (config/), apps (apps/), templates/ and static/
```

## End to end flow (signup to Instagram connected)

1. `/signup/` (Django form) creates the user and starts a session.
2. The user lands on `/connections/`, rendered from `services.connection_overview(user)`.
3. "Connect Instagram" posts to `/connections/instagram/connect/`, which redirects the browser to the Meta
   authorization URL (includes a signed `state`).
4. Meta redirects to `INSTAGRAM_REDIRECT_URI` = `/auth/instagram/callback?code&state` (a Django page view).
5. Backend validates state, exchanges code for a system user token, loads the shared Instagram account, stores an encrypted
   `SocialAccount`, then redirects to `/connections/` with a success message.
6. Login goes to `/studio/`, which opens the first account's dashboard (`DEV-content.md`).
