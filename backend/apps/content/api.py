"""JSON endpoints for the creator studio pages (fetch from static/js/app.js). Business logic stays in services.py."""

from urllib.parse import urlencode

from django.http import Http404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.content import services
from apps.core.exceptions import HookdError
from apps.core.http import api_login_required, json_error, json_ok
from apps.content.views import post_brain_form, publish_job_message
from apps.integrations import services as integration_services


def _next_url(name: str, pk: int, cursor: str) -> str | None:
    return f"{reverse(name, args=[pk])}?{urlencode({'after': cursor})}" if cursor else None


def _account(request, account_id: int):
    try:
        return integration_services.get_user_account(user=request.user, account_id=account_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc


def _post(request, post_id: int):
    try:
        return services.get_user_post(user=request.user, post_id=post_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc


def _items_html(request, template: str, items: list, name: str, **context) -> str:
    return "".join(render_to_string(template, {name: item, **context}, request=request) for item in items)


@api_login_required
@require_GET
def account_posts(request, account_id: int):
    """The next page of posts after `after`: `{html, next}` with the post cards."""
    account = _account(request, account_id)
    try:
        page = services.older_posts(account, after=request.GET.get("after", ""))
    except HookdError as exc:
        return json_error(exc.message)
    return json_ok(
        html=_items_html(request, "content/post_card.html", page.posts, "post"),
        next=_next_url("api-account-posts", account.pk, page.next_cursor),
    )


@api_login_required
@require_GET
def post_comments(request, post_id: int):
    """A page of a post's comments (the first one without `after`): `{html, next, count}`."""
    post = _post(request, post_id)
    try:
        comment_list = services.post_comments(post, after=request.GET.get("after", ""))
    except HookdError as exc:
        return json_error(exc.message)
    return json_ok(
        html=_items_html(request, "content/comment_item.html", comment_list.comments, "comment", account=post.account),
        next=_next_url("api-post-comments", post.pk, comment_list.next_cursor),
        count=len(comment_list.comments),
    )


@api_login_required
@require_GET
def post_preview(request, post_id: int):
    """The post in a modal (opened from the reply inbox): `{html}` with media, caption, counts and its Video brain.
    Uses the stored post, no platform call."""
    post = _post(request, post_id)
    html = render_to_string(
        "content/post_modal.html", {"post": post, "account": post.account, "brain_form": post_brain_form(post)}, request=request
    )
    return json_ok(html=html)


@api_login_required
@require_POST
def account_posts_refresh(request, account_id: int):
    """Reload the newest posts from the platform: `{html}` with the recent posts section."""
    account = _account(request, account_id)
    context = {"account": account, "can_publish": services.can_publish(account)}
    try:
        context["page"] = services.recent_posts(account, force=True)
    except HookdError as exc:
        return json_error(exc.message)
    return json_ok(html=render_to_string("content/post_list.html", context, request=request))


@api_login_required
@require_POST
def post_insights_refresh(request, post_id: int):
    """Fetch the post's metrics again: `{html}` with the insights block."""
    post = _post(request, post_id)
    context = {"post": post}
    try:
        context["post"] = services.post_insights(post, force=True)
    except HookdError as exc:
        context["insights_error"] = exc.message
    return json_ok(html=render_to_string("content/post_insights.html", context, request=request))


@api_login_required
@require_POST
def publish_check(request, job_id: int):
    """Check a processing publish job: `{html}` with its updated row and a message."""
    try:
        job = services.get_user_publish_job(user=request.user, job_id=job_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc
    job = services.advance_publish_job(job)
    tone, text = publish_job_message(job)
    return json_ok(
        html=render_to_string("content/job_item.html", {"job": job}, request=request), message=text, message_kind=tone
    )
