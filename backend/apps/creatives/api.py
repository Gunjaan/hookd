"""JSON endpoints for the draft page (fetch from static/js/app.js). Business logic stays in services.py."""

from django.http import Http404
from django.views.decorators.http import require_GET, require_POST

from apps.core.exceptions import HookdError
from apps.core.http import api_login_required, json_error, json_ok
from apps.creatives import services
from apps.creatives.exceptions import CreativeNotFound, SlideNotFound, SlideVersionNotFound
from apps.creatives.forms import CopyForm, RewriteForm, brain_answers
from apps.creatives.views import TWEAK_MAX_LENGTH, draft_state

COPY_INVALID = "Caption must be under 2200 characters."


def _creative(request, creative_id: int):
    try:
        return services.get_user_creative(user=request.user, creative_id=creative_id)
    except HookdError as exc:
        raise Http404(exc.message) from exc


def _state(request, creative_id: int) -> dict:
    creative = services.expire_stale_work(_creative(request, creative_id))
    return draft_state(request, creative, list(creative.slides.all()))


def _copy_answer(creative, message: str):
    return json_ok(
        caption=creative.caption,
        hashtags=creative.hashtags,
        full_caption=services.full_caption(creative.caption, creative.hashtags),
        message=message,
    )


@api_login_required
@require_GET
def draft_progress(request, creative_id: int):
    """Status, title, alert and photos of a draft, polled while it is working."""
    return json_ok(**_state(request, creative_id))


@api_login_required
@require_POST
def draft_copy(request, creative_id: int):
    """Save draft: caption, hashtags and answers. Answers with the normalized copy."""
    form = CopyForm(request.POST)
    if not form.is_valid():
        return json_error(COPY_INVALID)
    try:
        creative = services.update_copy(
            user=request.user, creative_id=creative_id, answers=brain_answers(request.POST), **form.cleaned_data
        )
    except CreativeNotFound as exc:
        raise Http404(exc.message) from exc
    except HookdError as exc:
        return json_error(exc.message)
    return _copy_answer(creative, "Draft saved.")


@api_login_required
@require_POST
def draft_rewrite(request, creative_id: int):
    """One text call for a new caption. The posted copy and answers are saved first."""
    form = CopyForm(request.POST)
    if not form.is_valid():
        return json_error(COPY_INVALID)
    rewrite = RewriteForm(request.POST)
    instruction = rewrite.cleaned_data["instruction"] if rewrite.is_valid() else ""
    copy = {**form.cleaned_data, "answers": brain_answers(request.POST)}
    try:
        creative = services.rewrite_caption(user=request.user, creative_id=creative_id, instruction=instruction, copy=copy)
    except CreativeNotFound as exc:
        raise Http404(exc.message) from exc
    except HookdError as exc:
        return json_error(exc.message)
    return _copy_answer(creative, "New caption written. Edit it as you like.")


@api_login_required
@require_POST
def slide_redo(request, slide_id: int):
    """Queue one photo render. Answers with the draft state so the page starts polling."""
    try:
        slide = services.regenerate_slide(
            user=request.user, slide_id=slide_id, tweak=request.POST.get("tweak", "")[:TWEAK_MAX_LENGTH]
        )
    except SlideNotFound as exc:
        raise Http404(exc.message) from exc
    except HookdError as exc:
        return json_error(exc.message)
    return json_ok(**_state(request, slide.creative_id))


@api_login_required
@require_POST
def version_use(request, version_id: int):
    """Put an earlier photo back (no AI call). Answers with the draft state."""
    try:
        slide = services.use_slide_version(user=request.user, version_id=version_id)
    except SlideVersionNotFound as exc:
        raise Http404(exc.message) from exc
    except HookdError as exc:
        return json_error(exc.message)
    return json_ok(**_state(request, slide.creative_id), message="Earlier photo is back in the post.")
