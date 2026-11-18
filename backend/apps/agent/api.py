"""JSON endpoints for the reply agent pages (data-api forms in static/js/app.js). Business logic stays in services.py."""

from django.http import Http404
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.agent import services
from apps.agent.constants import ReplyStatus
from apps.agent.forms import AgentEnabledForm, AgentSettingsForm, AnswerForm, ApproveForm, PostBrainForm, PreviewForm, TicketForm
from apps.agent.views import QUESTIONS_TAB, inbox_list_context, inbox_tabs, status_context
from apps.content import services as content_services
from apps.core.exceptions import HookdError
from apps.core.http import api_login_required, json_error, json_ok
from apps.integrations import services as integration_services


def _found(getter, **kwargs):
    try:
        return getter(**kwargs)
    except HookdError as exc:
        raise Http404(exc.message) from exc


def _form_error(form) -> str:
    return next(iter(form.errors.values()))[0]


@api_login_required
@require_POST
def account_settings(request, account_id: int):
    account = _found(integration_services.get_user_account, user=request.user, account_id=account_id)
    form = AgentSettingsForm(request.POST)
    if not form.is_valid():
        return json_error(_form_error(form))
    services.update_profile(account, **form.cleaned_data)
    return json_ok(message="Agent settings saved.")


@api_login_required
@require_POST
def account_enabled(request, account_id: int):
    """The agent page's on or off switch, saved at once. Redraws the status bar."""
    account = _found(integration_services.get_user_account, user=request.user, account_id=account_id)
    form = AgentEnabledForm(request.POST)
    form.is_valid()
    profile = services.update_profile(account, enabled=form.cleaned_data["enabled"])
    status = render_to_string("agent/status_bar.html", status_context(request.user, account, profile), request=request)
    message = "Hooky is on." if profile.enabled else "Hooky is off. New comments wait for you."
    return json_ok(message=message, fragments={"agent-status": status})


@api_login_required
@require_POST
def preview(request, account_id: int):
    """What the agent would do with a comment, as a dry run: `{html}` with each step it took. Nothing is saved or
    sent."""
    account = _found(integration_services.get_user_account, user=request.user, account_id=account_id)
    posts = services.preview_posts(account)
    form = PreviewForm(request.POST, posts=posts)
    if not form.is_valid():
        return json_error(_form_error(form))
    post = next((item for item in posts if item.pk == form.cleaned_data["post"]), None)
    try:
        result = services.preview_run(
            account=account,
            text=form.cleaned_data["text"],
            post=post,
            voice=form.cleaned_data["voice"] if "voice" in request.POST else None,
            knowledge=form.cleaned_data["knowledge"] if "knowledge" in request.POST else None,
        )
    except HookdError as exc:
        return json_error(exc.message)
    return json_ok(html=render_to_string("agent/preview_result.html", {"result": result}, request=request))


@api_login_required
@require_POST
def post_brain(request, post_id: int):
    post = _found(content_services.get_user_post, user=request.user, post_id=post_id)
    form = PostBrainForm(request.POST)
    if not form.is_valid():
        return json_error(_form_error(form))
    services.save_post_brain(post, **form.cleaned_data)
    return json_ok(message="Video brain saved.")


def _reply_done(request, status: str, message: str):
    """The card leaves the list (`html` empty); tabs, the sidebar badge and an emptied list are redrawn."""
    tabs = inbox_tabs(request.user)
    fragments = {
        "inbox-tabs": render_to_string("agent/inbox_tabs.html", {"tabs": tabs, "status": status}, request=request),
        "nav-replies-count": render_to_string(
            "components/replies_badge.html",
            {"agent_pending_count": services.attention_count(request.user)},
            request=request,
        ),
    }
    if not next(count for value, _, count in tabs if value == status):
        fragments["inbox-list"] = render_to_string(
            "agent/inbox_list.html", inbox_list_context(request.user, status), request=request
        )
    return json_ok(html="", fragments=fragments, message=message)


@api_login_required
@require_POST
def reply_approve(request, reply_id: int):
    reply = _found(services.get_user_reply, user=request.user, reply_id=reply_id)
    form = ApproveForm(request.POST)
    if not form.is_valid():
        return json_error("Write a reply before approving.")
    try:
        reply = services.approve_reply(reply, form.cleaned_data["text"])
    except HookdError as exc:
        return json_error(exc.message)
    sends_at = timezone.localtime(reply.scheduled_for)
    return _reply_done(
        request,
        ReplyStatus.PENDING_REVIEW,
        f"Reply scheduled for {sends_at:%H:%M}. Hookd spaces replies out to stay within Instagram's limits.",
    )


@api_login_required
@require_POST
def reply_reject(request, reply_id: int):
    reply = _found(services.get_user_reply, user=request.user, reply_id=reply_id)
    try:
        services.reject_reply(reply)
    except HookdError as exc:
        return json_error(exc.message)
    return _reply_done(request, ReplyStatus.PENDING_REVIEW, "Reply discarded.")


@api_login_required
@require_POST
def reply_retry(request, reply_id: int):
    reply = _found(services.get_user_reply, user=request.user, reply_id=reply_id)
    try:
        services.retry_reply(reply)
    except HookdError as exc:
        return json_error(exc.message)
    return _reply_done(request, ReplyStatus.FAILED, "Reply scheduled again.")


@api_login_required
@require_POST
def fact_forget(request, fact_id: int):
    """Remove a fact Hooky learned on its own. Empty `html` removes its row."""
    _found(services.forget_fact, user=request.user, fact_id=fact_id)
    return json_ok(html="", message="Hooky forgot that.")


@api_login_required
@require_POST
def dm_approve(request, dm_id: int):
    dm = _found(services.get_user_dm, user=request.user, dm_id=dm_id)
    form = ApproveForm(request.POST)
    if not form.is_valid():
        return json_error("Write a message before approving.")
    try:
        dm = services.approve_dm(dm, form.cleaned_data["text"])
    except HookdError as exc:
        return json_error(exc.message)
    sends_at = timezone.localtime(dm.scheduled_for)
    return _reply_done(request, ReplyStatus.PENDING_REVIEW, f"DM scheduled for {sends_at:%H:%M}.")


@api_login_required
@require_POST
def dm_reject(request, dm_id: int):
    dm = _found(services.get_user_dm, user=request.user, dm_id=dm_id)
    try:
        services.reject_dm(dm)
    except HookdError as exc:
        return json_error(exc.message)
    return _reply_done(request, ReplyStatus.PENDING_REVIEW, "DM discarded.")


@api_login_required
@require_POST
def question_answer(request, question_id: int):
    """The seller answers Hooky's question: the card leaves and Hooky carries on with the buyer."""
    question = _found(services.get_user_question, user=request.user, question_id=question_id)
    form = AnswerForm(request.POST)
    if not form.is_valid():
        return json_error("Write an answer first.")
    try:
        services.answer_question(question, form.cleaned_data["answer"])
    except HookdError as exc:
        return json_error(exc.message)
    return _reply_done(request, QUESTIONS_TAB, "Thanks! Hooky is answering the buyer now.")


@api_login_required
@require_POST
def question_dismiss(request, question_id: int):
    question = _found(services.get_user_question, user=request.user, question_id=question_id)
    try:
        services.dismiss_question(question)
    except HookdError as exc:
        return json_error(exc.message)
    return _reply_done(request, QUESTIONS_TAB, "Question dismissed. Hooky will not answer the buyer about it.")


@api_login_required
@require_POST
def ticket_update(request, ticket_id: int):
    """Status and the note for the buyer, saved from the Tickets page."""
    ticket = _found(services.get_user_ticket, user=request.user, ticket_id=ticket_id)
    form = TicketForm(request.POST, prefix=f"t{ticket.pk}")
    if not form.is_valid():
        return json_error(_form_error(form))
    services.update_ticket(ticket, **form.cleaned_data)
    return json_ok(message="Ticket saved. Hooky tells the buyer this status when they ask.")
