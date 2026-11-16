"""Post studio pages (session auth). Business logic stays in services.py."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.content.views import announce_publish_job
from apps.core.exceptions import HookdError
from apps.creatives import services
from apps.creatives.forms import ANSWER_MAX_LENGTH, CopyForm, CreativeForm, PublishForm, RedoSlideForm, RewriteForm, brain_answers

FILE_KINDS = ("slide", "source")


def _user_creative(request, creative_id: int):
    try:
        return services.get_user_creative(user=request.user, creative_id=creative_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc


def _back(creative_id: int):
    return redirect("creative-detail", creative_id=creative_id)


@login_required
def creative_new(request):
    form = CreativeForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            creative = services.start_creative(
                user=request.user,
                photos=form.cleaned_data["photos"],
                look=form.cleaned_data["look"],
                format_choice=form.cleaned_data["format_choice"],
                notes=form.cleaned_data["notes"],
            )
        except HookdError as exc:
            messages.error(request, exc.message)
        else:
            return _back(creative.pk)
    return render(request, "creatives/new.html", {"nav_section": "publishing", "form": form})


@login_required
def creative_detail(request, creative_id: int):
    creative = services.expire_stale_work(_user_creative(request, creative_id))
    slides = list(creative.slides.all())
    accounts = services.publish_accounts(request.user)
    return render(
        request,
        "creatives/detail.html",
        {
            "nav_section": "publishing",
            "creative": creative,
            "slides": slides,
            "ready_count": sum(1 for slide in slides if slide.status == slide.Status.READY),
            "rendering": any(slide.status == slide.Status.PENDING for slide in slides),
            "copy_form": CopyForm(initial={"caption": creative.caption, "hashtags": creative.hashtags}),
            "rewrite_form": RewriteForm(),
            "redo_form": RedoSlideForm(),
            "publish_form": PublishForm(accounts=accounts),
            "accounts": accounts,
            "full_caption": services.full_caption(creative.caption, creative.hashtags),
            "answer_max_length": ANSWER_MAX_LENGTH,
        },
    )


@login_required
@require_POST
def creative_copy(request, creative_id: int):
    form = CopyForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Caption must be under 2200 characters.")
        return _back(creative_id)
    try:
        services.update_copy(
            user=request.user, creative_id=creative_id, answers=brain_answers(request.POST), **form.cleaned_data
        )
    except HookdError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "Draft saved.")
    return _back(creative_id)


@login_required
@require_POST
def creative_rewrite(request, creative_id: int):
    form = RewriteForm(request.POST)
    instruction = form.cleaned_data["instruction"] if form.is_valid() else ""
    try:
        services.rewrite_caption(user=request.user, creative_id=creative_id, instruction=instruction)
    except HookdError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "New caption written. Edit it as you like.")
    return _back(creative_id)


@login_required
@require_POST
def creative_slide_redo(request, slide_id: int):
    form = RedoSlideForm(request.POST)
    tweak = form.cleaned_data["tweak"] if form.is_valid() else ""
    try:
        slide = services.regenerate_slide(user=request.user, slide_id=slide_id, tweak=tweak)
    except HookdError as exc:
        raise Http404(exc.message) from exc
    return _back(slide.creative_id)


@login_required
@require_POST
def creative_publish(request, creative_id: int):
    """The draft form's "Post now" button: saves the edited caption and hashtags, then publishes."""
    copy_form = CopyForm(request.POST)
    publish_form = PublishForm(request.POST, accounts=services.publish_accounts(request.user))
    if not copy_form.is_valid() or not publish_form.is_valid():
        messages.error(request, "Choose an account and keep the caption under 2200 characters.")
        return _back(creative_id)
    try:
        services.update_copy(
            user=request.user, creative_id=creative_id, answers=brain_answers(request.POST), **copy_form.cleaned_data
        )
        job = services.publish_creative(
            user=request.user, creative_id=creative_id, account_id=publish_form.cleaned_data["account"]
        )
    except HookdError as exc:
        messages.error(request, exc.message)
        return _back(creative_id)
    announce_publish_job(request, job)
    return redirect("publishing")


@login_required
@require_POST
def creative_delete(request, creative_id: int):
    try:
        services.delete_creative(user=request.user, creative_id=creative_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc
    messages.success(request, "Draft deleted.")
    return redirect("publishing")


@login_required
def creative_file(request, kind: str, file_id: int):
    if kind not in FILE_KINDS:
        raise Http404()
    try:
        field = services.user_file(user=request.user, kind=kind, file_id=file_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc
    response = FileResponse(field.open("rb"), content_type="image/jpeg")
    if request.GET.get("download"):
        response["Content-Disposition"] = f'attachment; filename="hookd-{kind}-{file_id}.jpg"'
    return response
