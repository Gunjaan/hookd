"""Reply agent pages (session auth): settings, preview, the inbox and support tickets. Their actions live in api.py."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render

from apps.agent import services
from apps.agent.constants import ReplyStatus, TicketStatus
from apps.agent.forms import VOICE_PICKS, AgentEnabledForm, AgentSettingsForm, PreviewForm, TicketForm
from apps.core.exceptions import HookdError
from apps.integrations import services as integration_services

# The inbox's first tab lists Hooky's questions to the seller; the others list public replies by status.
QUESTIONS_TAB = "questions"
INBOX_TABS = (
    QUESTIONS_TAB,
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
        "agent_pending_count": services.attention_count(user),
    }


@login_required
def account_settings(request, account_id: int):
    account = _user_account(request, account_id)
    profile = services.get_profile(account)
    form = AgentSettingsForm(
        initial={
            "voice": profile.voice,
            "knowledge": profile.knowledge,
            "reply_to_threads": profile.reply_to_threads,
            "approve_before_sending": profile.approve_before_sending,
        },
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
            "learned_facts": services.account_learned_facts(account),
            "post_fact_count": services.post_learned_fact_count(account),
        },
    )


def inbox_tabs(user) -> list[tuple[str, str, int]]:
    """(status, label, count) for every inbox tab. Needs review also counts the DMs waiting for approval."""
    counts = services.inbox_counts(user)
    counts[QUESTIONS_TAB] = services.open_question_count(user)
    counts[ReplyStatus.PENDING_REVIEW] = counts.get(ReplyStatus.PENDING_REVIEW, 0) + services.pending_dm_count(user)
    return [
        (value, "Hooky asks" if value == QUESTIONS_TAB else ReplyStatus(value).label, counts.get(value, 0))
        for value in INBOX_TABS
    ]


def inbox_list_context(user, status: str) -> dict:
    """Context of agent/inbox_list.html for a tab, also redrawn by the actions in api.py."""
    if status == QUESTIONS_TAB:
        return {"status": status, "questions": services.open_questions(user), "replies": [], "dms": []}
    dms = services.pending_dms(user) if status == ReplyStatus.PENDING_REVIEW else []
    return {"status": status, "questions": [], "replies": services.inbox(user, status), "dms": dms}


@login_required
def inbox(request):
    status = request.GET.get("status")
    if status not in INBOX_TABS:
        status = QUESTIONS_TAB if services.open_question_count(request.user) else ReplyStatus.PENDING_REVIEW
    return _page(
        request,
        "agent/inbox.html",
        {"nav_section": "inbox", "tabs": inbox_tabs(request.user), **inbox_list_context(request.user, status)},
    )


def ticket_tabs(user) -> list[tuple[str, str, int]]:
    counts = services.ticket_counts(user)
    return [(value, label, counts.get(value, 0)) for value, label in TicketStatus.choices]


@login_required
def tickets(request):
    status = request.GET.get("status", TicketStatus.OPEN)
    if status not in TicketStatus.values:
        status = TicketStatus.OPEN
    rows = services.tickets(request.user, status)
    for ticket in rows:
        ticket.form = TicketForm(initial={"status": ticket.status, "seller_note": ticket.seller_note}, prefix=f"t{ticket.pk}")
    return _page(
        request,
        "agent/tickets.html",
        {"nav_section": "inbox", "status": status, "tabs": ticket_tabs(request.user), "tickets": rows},
    )
