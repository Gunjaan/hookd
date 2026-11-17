"""Reply agent pages (session auth): settings, preview and the reply inbox. Their actions live in api.py."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render

from apps.agent import services
from apps.agent.constants import ReplyStatus
from apps.agent.forms import VOICE_PICKS, AgentEnabledForm, AgentSettingsForm, PreviewForm
from apps.core.exceptions import HookdError
from apps.integrations import services as integration_services

INBOX_TABS = (
    ReplyStatus.PENDING_REVIEW,
    ReplyStatus.QUEUED,
    ReplyStatus.SENT,
    ReplyStatus.SKIPPED,
    ReplyStatus.FAILED,
    ReplyStatus.REJECTED,
)


def _user_account(request, account_id: int):
    try:
        return integration_services.get_user_account(user=request.user, account_id=account_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc


def _page(request, template: str, context: dict):
    context.setdefault("nav_section", "agent")
    return render(request, template, context)


@login_required
def agent_home(request):
    accounts = services.agent_accounts(request.user)
    if not accounts:
        messages.info(request, "Connect an account to set up the reply agent.")
        return redirect("connections")
    return redirect("agent-account", account_id=accounts[0].pk)


def status_context(user, account, profile) -> dict:
    """Context of agent/status_bar.html, also redrawn by api.account_enabled."""
    return {
        "account": account,
        "profile": profile,
        "enabled_form": AgentEnabledForm(initial={"enabled": profile.enabled}),
        "is_paused": services.is_paused(profile),
        "sent_this_week": services.sent_reply_count(account, days=7),
        "agent_pending_count": services.pending_review_count(user),
    }


@login_required
def account_settings(request, account_id: int):
    account = _user_account(request, account_id)
    profile = services.get_profile(account)
    form = AgentSettingsForm(
        initial={"voice": profile.voice, "knowledge": profile.knowledge, "reply_to_threads": profile.reply_to_threads},
    )
    return _page(
        request,
        "agent/account.html",
        {
            **status_context(request.user, account, profile),
            "accounts": services.agent_accounts(request.user),
            "form": form,
            "preview_form": PreviewForm(posts=services.preview_posts(account)),
            "voice_picks": VOICE_PICKS,
            "supports_replies": services.supports_replies(account),
            "limits": services.sending_limits(),
        },
    )


def inbox_tabs(user) -> list[tuple[str, str, int]]:
    """(status, label, count) for every inbox tab."""
    counts = services.inbox_counts(user)
    return [(value, ReplyStatus(value).label, counts.get(value, 0)) for value in INBOX_TABS]


@login_required
def inbox(request):
    status = request.GET.get("status", ReplyStatus.PENDING_REVIEW)
    if status not in INBOX_TABS:
        status = ReplyStatus.PENDING_REVIEW
    return _page(
        request,
        "agent/inbox.html",
        {
            "nav_section": "inbox",
            "status": status,
            "tabs": inbox_tabs(request.user),
            "replies": services.inbox(request.user, status),
        },
    )
