# DEV: Auth (`backend/apps/accounts`)

Email and password accounts with Django session auth. Pages and routes are in `DEV-web.md`.

## Signup is closed (access requests)

`SIGNUPS_OPEN = False` in `config/settings.py`. While it is false, `/signup/` does not create accounts: it shows
`AccessRequestForm` (name, email, Instagram handle, optional phone or WhatsApp, what they create or sell). A valid
submit calls `services.request_access`, which queues the Celery task `send_access_request`; the task emails the
details to `SUPPORT_EMAIL` (`support@hookd.world`) with `Reply-To` set to the applicant, then the browser goes to
`/signup/requested/` ("You are on the list"). No user row is created. Accounts are made by the team in the Django admin.

The landing page buttons, its closing section and the login page link switch between "Request access" and the normal
signup copy based on the same setting. Set `SIGNUPS_OPEN = True` to restore self serve signup (`SignupForm`).

Email is sent through GoDaddy Workspace Email SMTP (`EMAIL_*` env vars, setup in `DEV-infrastructure.md`); without `EMAIL_HOST` the mail is printed to the Celery
worker log.

## Model

`User` (`AUTH_USER_MODEL = 'accounts.User'`), built on `AbstractBaseUser + PermissionsMixin`:

| Field | Notes |
|---|---|
| `email` | unique, login field, stored lowercased |
| `name` | optional display name |
| `is_active`, `is_staff`, `date_joined` | standard |

`UserManager.create_user` / `create_superuser`. Admin: `UserAdmin` with custom forms in `forms.py`.

## Files

| File | Contents |
|---|---|
| `model_querysets.py` | `email_is_registered`, `create_user` |
| `forms.py` | Admin `UserCreationForm`/`UserChangeForm`; `SignupForm` (lowercased unique email, Django password validators, errors under `password`); `AccessRequestForm` (lowercased email, Instagram handle normalized to `@handle`); `LoginForm` (`authenticate`, sets `form.user`, error "Invalid email or password." as a non field error) |
| `services.py` | `request_access(details)` queues the email task; `send_access_request_email(details)` sends it |
| `tasks.py` | `send_access_request(details)` (retries on SMTP and network errors) |
| `views.py` | `login_page`, `signup_page` (guest only, redirect signed in users; access request form while signup is closed), `access_requested_page`, `logout_page` (POST only, back to the landing page). `_safe_next` allows only same host `?next=` targets |
| `urls.py` | `/login/`, `/signup/`, `/signup/requested/`, `/logout/` (no namespace) |
| `templates/accounts/` | `login.html`, `signup.html`, `request_access.html`, `access_requested.html` on `layouts/auth.html` |
| `tests.py` | `AccessRequestTests`: closed signup form, email to support with reply to, no user created, field errors. `AuthPageTests` (with `SIGNUPS_OPEN=True`): signup logs in, field errors, bad password, safe `next`, logout is POST only and ends the session |

Settings: `LOGIN_URL = 'login'`, `LOGIN_REDIRECT_URL = 'studio'`, `SIGNUPS_OPEN`, `SUPPORT_EMAIL`. Login, signup and
access requests are not rate limited yet.
