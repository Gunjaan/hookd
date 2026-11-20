# How I built Hookd (and finally got it online)

A short, honest write up of how Hookd went from an idea I kept carrying around to a live app at
[hookd.world](https://hookd.world).

## The idea was not new

I had this idea in my head for a long time. A friend of mine runs a small shop on Instagram, and every time a post did
well, the same thing happened: hundreds of comments and DMs asking "price?", "size?", "do you ship to Pune?", in three
or four different languages. Most of them went unanswered, and every unanswered question was a lost sale. Hiring
someone to sit in the inbox all day was not an option for a small seller.

So the idea was simple: every post gets its own AI manager that answers in the seller's voice and in the buyer's
language, and asks the seller when it does not know something instead of making it up.

I knew it was a real problem. What I did not have was the push to actually build it.

## The push

That push came when I got selected for the Anthropic and GoDaddy credits. Claude credits for the AI part, GoDaddy for
the domain and hosting. At that point I had no more excuses, so I decided to give it a proper try.

## What I already knew

The backend was the comfortable part. I work with Django every day, so models, services, Celery tasks, Postgres and
Redis were all familiar ground. I kept the stack boring on purpose: Django templates, plain HTML, CSS and JS, no
frontend framework, no build step. Everything runs in Docker containers.

## What I did not know: hosting, domain and DNS

This is where I was honestly out of my depth. At work there is always a DevOps team that handles servers, domains and
certificates. Here it was just me.

GoDaddy helped me a lot here, and I mean that (trust me). I bought `hookd.world` and a VPS from them, and having the
domain, DNS and server in one place made things much less scary. On top of that:

- **DNS:** pointing the domain and `www` at the VPS was all done from the GoDaddy DNS panel.
- **Email:** `support@hookd.world` runs on GoDaddy Workspace Email, and the app sends mail through their SMTP server.
- **Agent identity:** Hooky, the reply agent, is registered on GoDaddy's Agent Name Service as
  [`ans://v1.0.0.hookd.world`](https://transparency.ans.godaddy.com/v1/agents/734f2707-6867-457d-abf1-0f0dc6451a62),
  with its agent card served at [`/.well-known/agent-card.json`](https://hookd.world/.well-known/agent-card.json).
  It felt good to have the agent verifiable, not just "trust me, it is a bot".

For the server itself I just asked Claude, and it walked me through it step by step: setting up the Ubuntu VPS,
installing Docker, locking it down with `ufw`, putting Caddy in front for automatic HTTPS with Let's Encrypt, keeping
gunicorn only on `127.0.0.1`, serving static files from Caddy, and fixing the small permission issues that always show
up. When something broke, I pasted the error, and we fixed it together. Most of the time it really felt like being hand
guided by a senior engineer who never got tired of my questions.

## How I used Claude

Claude shows up in two places in this project.

**Building it.** I used Claude Code as a pair programmer for the whole thing. One thing that worked really well: the
repo has a `brainforagents/` folder with one `DEV-<feature>.md` file per feature, plus an `AGENTS.md` with the rules
(where ORM code lives, no raw colors in CSS, every change updates its DEV doc, and so on). Claude reads those first and
keeps them up to date, so every new session starts with full context instead of me explaining the project again.

**Inside the product.** Hooky runs as an agent loop on Claude Haiku. For every new comment or buyer DM it looks up
facts about the product, replies publicly or privately within Meta's messaging windows, asks the seller when it is not
sure, learns the answer for next time, and opens support tickets when needed. The post studio also uses Claude to read
product photos and write captions and hashtags.

## The design came from corporate

The design and structure of the project is not something I invented over a weekend. It is what I picked up working in
corporate teams, fed straight into this:

- Thin views, business logic in `services.py`, all database access in one `model_querysets.py` per app.
- Domain errors with clear, user facing messages instead of random exceptions.
- A provider interface for platforms, so adding YouTube later does not mean `if platform == ...` everywhere.
- A small design system with tokens and reusable partials, so the UI stays consistent.
- Secrets only in env vars, encrypted tokens in the database, tests and `manage.py check` before every change.

Claude was fast, but these rules are what kept it fast without turning the codebase into a mess.

## Where it stands

Hookd is live at [hookd.world](https://hookd.world). Instagram is connected, Hooky replies to comments and DMs, and the
post studio turns product photos into ready to post drafts. Meta's app review is still pending, so for now only tester
accounts can connect.

Looking back, the idea was mine for years, the Django part was mine from work, but getting it online and shipped in
this short time would not have happened without GoDaddy making the infrastructure approachable and Claude guiding me
through every part I did not know.
