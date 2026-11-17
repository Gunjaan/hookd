"""Post studio pages (session auth). Business logic stays in services.py."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.formats import date_format
from django.utils.timezone import localtime
from django.views.decorators.http import require_POST

from apps.content.views import announce_publish_job
from apps.core.exceptions import HookdError
from apps.creatives import services
from apps.creatives.forms import ANSWER_MAX_LENGTH, CopyForm, CreativeForm, PublishForm, RedoSlideForm, RewriteForm, brain_answers

FILE_KINDS = ("slide", "version", "source")
TWEAK_MAX_LENGTH = RedoSlideForm.base_fields["tweak"].max_length
TITLES = {
    "ready": "Your post is ready",
    "published": "Posted",
    "failed": "This draft did not work",
}


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


def can_change_photos(creative) -> bool:
    """Redo and "Use this one" only while nothing renders and the draft is not posted (the services refuse then)."""
    return creative.status in (creative.Status.READY, creative.Status.FAILED) and bool(creative.brief)


def draft_story(creative, slides: list) -> list[dict]:
    """The five steps from product photo to a trained agent, each `done`, `active` (where the draft is now) or `todo`."""
    status = creative.Status
    photos_ready = bool(slides) and all(slide.status == slide.Status.READY for slide in slides)
    making = creative.is_working or any(slide.status == slide.Status.PENDING for slide in slides)
    posted = creative.status == status.PUBLISHED
    job = creative.publish_job
    answered = len(creative.answered_questions)
    sources = len(creative.sources.all())

    if making:
        photos = ("active", "Making them now")
    elif photos_ready:
        photos = ("done", f"{len(slides)} photo{'s' if len(slides) != 1 else ''}, {creative.get_look_display().lower()}")
    else:
        photos = ("active", "Some did not work, redo them")

    if creative.caption:
        caption = ("done", "Posted with it" if posted else "Written, edit it any time")
    else:
        caption = ("todo", "Written with the photos")

    if posted and job:
        when = date_format(localtime(job.published_at or job.created_at), "j M")
        posted_step = {
            job.Status.PUBLISHED: ("done", f"@{job.account.username}, {when}"),
            job.Status.PROCESSING: ("active", f"Processing on @{job.account.username}"),
        }.get(job.status, ("active", "Did not go out, see below"))
    elif creative.status == status.READY:
        posted_step = ("active", "Post when you are happy")
    else:
        posted_step = ("todo", "Once the photos are done")

    live = posted and job is not None and job.status == job.Status.PUBLISHED
    if creative.brain_saved_at and answered:
        brain = ("done", f"{answered} answer{'s' if answered != 1 else ''} saved for replies")
    elif live:
        brain = ("active", "No answers yet, Hooky asks you")
    elif posted:
        brain = ("todo", "Saved as soon as the post is live")
    else:
        brain = ("todo", "Answer the questions, saved when posted")

    return [
        {"title": "Your photo", "state": "done", "detail": f"{sources} added" if sources != 1 else "1 added"},
        {"title": "Hookd made photos", "state": photos[0], "detail": photos[1]},
        {"title": "Caption and hashtags", "state": caption[0], "detail": caption[1]},
        {"title": "Posted", "state": posted_step[0], "detail": posted_step[1]},
        {"title": "Hooky knows it", "state": brain[0], "detail": brain[1]},
    ]


def draft_state(request, creative, slides: list) -> dict:
    """What the draft page shows about progress. Rendered into the page and sent as JSON by the progress endpoint."""
    rendering = any(slide.status == slide.Status.PENDING for slide in slides)
    ready = sum(1 for slide in slides if slide.status == slide.Status.READY)
    if creative.status == creative.Status.FAILED and slides:
        alert = {"kind": "danger", "text": f'{creative.error} Use "Redo this photo" to try again.'}
    elif rendering:
        alert = {"kind": "info", "text": f"Making photo {ready + 1} of {len(slides)}. The page updates by itself, your edits stay."}
    else:
        alert = None
    changeable = can_change_photos(creative)
    items = []
    for number, slide in enumerate(slides, start=1):
        # Changes when the photo or its controls change, so the page swaps only those slides.
        key = f"{slide.status}-{slide.updated_at.timestamp()}-{len(slide.versions.all())}-{changeable}"
        context = {
            "slide": slide, "slide_key": key, "number": number, "total": len(slides),
            "can_change_photos": changeable, "tweak_max_length": TWEAK_MAX_LENGTH,
        }
        items.append({"id": slide.pk, "key": key, "html": render_to_string("creatives/slide.html", context, request=request)})
    story_html = render_to_string(
        "creatives/story.html", {"creative": creative, "story": draft_story(creative, slides)}, request=request
    )
    return {
        "status": creative.status,
        "title": TITLES.get(creative.status, "Making your post"),
        "story": story_html,
        "working": creative.is_working,
        "can_post": creative.status == creative.Status.READY,
        "alert": alert,
        "slides": items,
    }


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
            "state": draft_state(request, creative, slides),
            "copy_form": CopyForm(initial={"caption": creative.caption, "hashtags": creative.hashtags}),
            "rewrite_form": RewriteForm(),
            "publish_form": PublishForm(accounts=accounts),
            "accounts": accounts,
            "full_caption": services.full_caption(creative.caption, creative.hashtags),
            "answer_max_length": ANSWER_MAX_LENGTH,
        },
    )


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
