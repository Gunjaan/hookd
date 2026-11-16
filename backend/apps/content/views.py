"""Creator studio pages (session auth). Business logic stays in services.py."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.agent import services as agent_services
from apps.agent.forms import PostBrainForm
from apps.content import services
from apps.content.forms import ComposeForm
from apps.core.exceptions import HookdError
from apps.creatives import services as creative_services
from apps.integrations import services as integration_services


def _user_account(request, account_id: int):
    try:
        return integration_services.get_user_account(user=request.user, account_id=account_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc


def _user_post(request, post_id: int):
    try:
        return services.get_user_post(user=request.user, post_id=post_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc


@login_required
def studio(request):
    accounts = services.studio_accounts(request.user)
    if not accounts:
        messages.info(request, "Connect an account to open the studio.")
        return redirect("connections")
    return redirect("studio-account", account_id=accounts[0].pk)


@login_required
def account_dashboard(request, account_id: int):
    account = _user_account(request, account_id)
    after = request.GET.get("after", "")
    context = {
        "nav_section": "studio",
        "account": account,
        "accounts": services.studio_accounts(request.user),
        "after": after,
        "can_publish": services.can_publish(account),
        "insights": [],
    }
    try:
        context["page"] = services.older_posts(account, after=after) if after else services.recent_posts(account)
    except HookdError as exc:
        context["page"] = services.PostPage(posts=[])
        context["posts_error"] = exc.message
    if not after:
        try:
            context["insights"] = services.account_insights(account)
        except HookdError as exc:
            context["insights_error"] = exc.message
    return render(request, "content/dashboard.html", context)


@login_required
@require_POST
def account_refresh(request, account_id: int):
    account = _user_account(request, account_id)
    try:
        services.recent_posts(account, force=True)
    except HookdError as exc:
        messages.error(request, exc.message)
    return redirect("studio-account", account_id=account.pk)


@login_required
def post_detail(request, post_id: int):
    post = _user_post(request, post_id)
    insights_error = None
    try:
        post = services.post_insights(post)
    except HookdError as exc:
        insights_error = exc.message
    brain = agent_services.get_post_brain(post)
    brain_form = PostBrainForm(
        initial={"knowledge": brain.knowledge, "agent_enabled": brain.agent_enabled} if brain else None
    )
    return render(
        request,
        "content/post_detail.html",
        {
            "nav_section": "studio",
            "post": post,
            "account": post.account,
            "insights_error": insights_error,
            "brain_form": brain_form,
        },
    )


@login_required
@require_POST
def post_insights_refresh(request, post_id: int):
    post = _user_post(request, post_id)
    try:
        services.post_insights(post, force=True)
    except HookdError as exc:
        messages.error(request, exc.message)
    return redirect("post-detail", post_id=post.pk)


@login_required
def post_comments(request, post_id: int):
    post = _user_post(request, post_id)
    context = {"nav_section": "studio", "post": post, "account": post.account}
    try:
        context["comment_list"] = services.post_comments(post, after=request.GET.get("after", ""))
    except HookdError as exc:
        context["comment_list"] = services.CommentList(comments=[])
        context["comments_error"] = exc.message
    return render(request, "content/post_comments.html", context)


@login_required
def compose(request, account_id: int):
    account = _user_account(request, account_id)
    if not services.can_publish(account):
        raise Http404("Publishing is not available for this account.")
    form = ComposeForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            job = services.create_publish_job(
                account=account,
                kind=form.cleaned_data["kind"],
                caption=form.cleaned_data["caption"],
                uploads=form.cleaned_data["media"],
            )
        except HookdError as exc:
            messages.error(request, exc.message)
        else:
            announce_publish_job(request, job)
            return redirect("publishing")

    quota = None
    quota_error = None
    try:
        quota = services.remaining_publish_quota(account)
    except HookdError as exc:
        quota_error = exc.message
    return render(
        request,
        "content/compose.html",
        {"nav_section": "studio", "account": account, "form": form, "quota": quota, "quota_error": quota_error},
    )


@login_required
def publishing(request):
    return render(
        request,
        "content/publishing.html",
        {
            "nav_section": "publishing",
            "jobs": services.list_publish_jobs(request.user),
            "creatives": creative_services.list_creatives(request.user),
        },
    )


@login_required
@require_POST
def publish_check(request, job_id: int):
    try:
        job = services.get_user_publish_job(user=request.user, job_id=job_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc
    announce_publish_job(request, services.advance_publish_job(job))
    return redirect("publishing")


def announce_publish_job(request, job) -> None:
    if job.status == job.Status.PUBLISHED:
        messages.success(request, "Published.")
    elif job.status == job.Status.FAILED:
        messages.error(request, f"Publishing failed: {job.error}")
    else:
        messages.info(request, "Uploaded. The platform is still processing it; Hookd publishes it as soon as it is ready.")


def publish_asset(request, token):
    """Public, unguessable URL the platform downloads uploads from. Only served while the job is processing."""
    asset = services.find_servable_asset(token)
    if asset is None:
        raise Http404()
    return FileResponse(asset.file.open("rb"), content_type=asset.content_type)
