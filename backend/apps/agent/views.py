"""Reply agent pages (session auth): settings, rules, video brain, preview and the reply inbox."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.agent import services
from apps.agent.constants import ReplyStatus
from apps.agent.forms import AgentSettingsForm, ApproveForm, PostBrainForm, PreviewForm, RuleForm
from apps.content import services as content_services
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


def _user_post(request, post_id: int):
    try:
        return content_services.get_user_post(user=request.user, post_id=post_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc


def _user_rule(request, rule_id: int):
    try:
        return services.get_user_rule(user=request.user, rule_id=rule_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc


def _user_reply(request, reply_id: int):
    try:
        return services.get_user_reply(user=request.user, reply_id=reply_id)
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


@login_required
def account_settings(request, account_id: int):
    account = _user_account(request, account_id)
    profile = services.get_profile(account)
    rules = services.list_rules(account)
    has_rules = bool(rules)
    form = AgentSettingsForm(
        request.POST or None, has_rules=has_rules, initial=AgentSettingsForm.initial_for(profile, has_rules=has_rules),
    )
    if request.method == "POST" and form.is_valid():
        services.update_profile(account, **form.profile_fields())
        messages.success(request, "Agent settings saved.")
        return redirect("agent-account", account_id=account.pk)
    return _page(
        request,
        "agent/account.html",
        {
            "account": account,
            "accounts": services.agent_accounts(request.user),
            "profile": profile,
            "form": form,
            "supports_replies": services.supports_replies(account),
            "is_paused": services.is_paused(profile),
            "limits": services.sending_limits(),
            "account_rules": [rule for rule in rules if rule.post_id is None],
            "post_rules": [rule for rule in rules if rule.post_id is not None],
        },
    )


@login_required
@require_POST
def starter_rules(request, account_id: int):
    account = _user_account(request, account_id)
    added = services.add_starter_rules(account)
    if added:
        messages.success(request, f"Added {added} ready made rule{'s' if added != 1 else ''}. They draft replies for you to approve until you switch them to 'Reply on its own'.")
    else:
        messages.info(request, "You already have all the ready made rules.")
    return redirect("agent-account", account_id=account.pk)


@login_required
def preview(request, account_id: int):
    account = _user_account(request, account_id)
    posts = services.preview_posts(account)
    form = PreviewForm(request.POST or None, posts=posts)
    result = None
    if request.method == "POST" and form.is_valid():
        post = next((item for item in posts if item.pk == form.cleaned_data["post"]), None)
        result = services.preview_reply(account=account, text=form.cleaned_data["text"], post=post)
    return _page(request, "agent/preview.html", {"account": account, "form": form, "result": result})


def _rule_page(request, *, account, post, form, rule=None):
    return _page(request, "agent/rule_form.html", {"account": account, "post": post, "form": form, "rule": rule})


def _after_rule(rule):
    if rule.post_id:
        return redirect("post-detail", post_id=rule.post_id)
    return redirect("agent-account", account_id=rule.account_id)


@login_required
def rule_new(request, account_id: int):
    account = _user_account(request, account_id)
    post = None
    post_id = request.GET.get("post", "")
    if post_id.isdigit():
        post = _user_post(request, int(post_id))
        if post.account_id != account.pk:
            raise Http404()
    form = RuleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rule = services.create_rule(account=account, post=post, **form.cleaned_data)
        messages.success(request, f'Rule "{rule.name}" added.')
        return _after_rule(rule)
    return _rule_page(request, account=account, post=post, form=form)


@login_required
def rule_edit(request, rule_id: int):
    rule = _user_rule(request, rule_id)
    initial = {
        "name": rule.name, "when": rule.when, "keywords": rule.keywords, "action": rule.action,
        "how_to_reply": rule.how_to_reply, "enabled": rule.enabled,
    }
    form = RuleForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        services.update_rule(rule, **form.cleaned_data)
        messages.success(request, f'Rule "{rule.name}" saved.')
        return _after_rule(rule)
    return _rule_page(request, account=rule.account, post=rule.post, form=form, rule=rule)


@login_required
@require_POST
def rule_delete(request, rule_id: int):
    rule = _user_rule(request, rule_id)
    services.delete_rule(rule)
    messages.success(request, f'Rule "{rule.name}" deleted.')
    return _after_rule(rule)


@login_required
@require_POST
def post_brain(request, post_id: int):
    post = _user_post(request, post_id)
    form = PostBrainForm(request.POST)
    if form.is_valid():
        services.save_post_brain(post, **form.cleaned_data)
        messages.success(request, "Video brain saved.")
    else:
        messages.error(request, form.errors.as_text())
    return redirect("post-detail", post_id=post.pk)


@login_required
def inbox(request):
    status = request.GET.get("status", ReplyStatus.PENDING_REVIEW)
    if status not in INBOX_TABS:
        status = ReplyStatus.PENDING_REVIEW
    counts = services.inbox_counts(request.user)
    tabs = [(value, ReplyStatus(value).label, counts.get(value, 0)) for value in INBOX_TABS]
    return _page(
        request,
        "agent/inbox.html",
        {"nav_section": "inbox", "status": status, "tabs": tabs, "replies": services.inbox(request.user, status)},
    )


def _back_to_inbox(status: str):
    response = redirect("agent-inbox")
    response["Location"] += f"?status={status}"
    return response


@login_required
@require_POST
def reply_approve(request, reply_id: int):
    reply = _user_reply(request, reply_id)
    form = ApproveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Write a reply before approving.")
        return _back_to_inbox(ReplyStatus.PENDING_REVIEW)
    try:
        reply = services.approve_reply(reply, form.cleaned_data["text"])
    except HookdError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, f"Reply scheduled for {timezone.localtime(reply.scheduled_for):%H:%M}. Hookd spaces replies out to stay within Instagram's limits.")
    return _back_to_inbox(ReplyStatus.PENDING_REVIEW)


@login_required
@require_POST
def reply_reject(request, reply_id: int):
    reply = _user_reply(request, reply_id)
    try:
        services.reject_reply(reply)
    except HookdError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "Reply discarded.")
    return _back_to_inbox(ReplyStatus.PENDING_REVIEW)


@login_required
@require_POST
def reply_retry(request, reply_id: int):
    reply = _user_reply(request, reply_id)
    try:
        services.retry_reply(reply)
    except HookdError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "Reply scheduled again.")
    return _back_to_inbox(ReplyStatus.FAILED)
