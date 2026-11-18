# DEV: Overview

Hookd gives every video its own AI community manager that replies in the creator's voice and the
commenter's language. Product plan: `hookd-plan.md` at the repo root.

## Current scope

Working today: **public landing page at `/`, login (self serve signup is closed: `/signup/` emails an access request
to support, see `DEV-auth.md`), connect an Instagram professional account, receive its new comments by webhook,
store its DMs, story replies and story mentions when the `messages` webhook is on,
the creator studio (recent posts, post and account insights, paged comments), publishing images, reels, carousels and stories, the post studio (product photos in, AI made post photos with an editable
caption and hashtags out, plus questions that teach the reply agent about the product; Claude writes, Gemini renders the photos), and
the reply agent Hooky (an agent loop on Claude Haiku for every new comment and buyer DM: looks facts up in brains that also fill themselves, replies publicly, sends private replies and DMs within Meta's windows, asks the seller and learns the answer, opens support tickets and reports their status, schedules follow-ups; a number guard and an optional approval mode, rate limited sending).**
Not built yet: YouTube, workflows, scheduled posts.

## Feature index

| File | Covers |
|---|---|
| `DEV-infrastructure.md` | deploy.py and Docker, Postgres, Redis, cache, env vars, cron, Celery, running and testing, production with Caddy |
| `DEV-auth.md` | `apps.accounts`: custom User, session login, signup, logout |
| `DEV-core.md` | `apps.core`: base model, encrypted field, `HookdError`, health check |
| `DEV-ai.md` | `apps.ai`: Claude and Gemini clients, record of every AI call (what was sent, answer, tokens, cost, purpose, user), cost admin |
| `DEV-integrations.md` | `apps.integrations`: provider interface, SocialAccount, connect flow, token refresh |
| `DEV-instagram.md` | Instagram provider: Unified Login (Facebook Login for Business), token lifecycle, Meta app setup |
| `DEV-comments.md` | `apps.comments`: Comment model, webhook endpoint, how comments arrive |
| `DEV-messaging.md` | `apps.messaging`: DMs, story replies and story mentions from the `messages` webhook, 24 hour reply window, hand off to the reply agent |
| `DEV-agent.md` | `apps.agent`: the agent loop and its tools, runs from comments and DMs, number guard, approval mode, seller questions, support tickets, follow-ups, brains and learned brain, model interface (Claude, simulated fallback), inbox and Tickets page, Meta safe sending of replies and DMs |
| `DEV-content.md` | `apps.content`: studio dashboard, lazy loading, Post, insights, comments paging, publishing |
| `DEV-creatives.md` | `apps.creatives`: post studio, product photos to ready to post drafts (Claude copy engine, Gemini image engine, prompts, token rules) |
| `DEV-web.md` | Server rendered UI: Django templates, design system CSS, page routes, session auth |

## Repository layout

```
hookd/
  AGENTS.md, CLAUDE.md        Agent rules (CLAUDE.md imports AGENTS.md)
  README.md                   Human quick start
  deploy.py                   Docker helper: build, run, deploy, install-cron
  Caddyfile                   Production web server: HTTPS, static files, proxy to gunicorn
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
