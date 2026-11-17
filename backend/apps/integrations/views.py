"""Connections pages (session auth), the OAuth callback and the webhook. Account actions live in api.py."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.exceptions import HookdError
from apps.integrations import services
from apps.integrations.exceptions import ProviderNotFound, WebhookVerificationError

logger = logging.getLogger(__name__)


@login_required
def connections_page(request):
    return render(request, "integrations/connections.html", {"providers": services.connection_overview(request.user)})


@login_required
@require_POST
def connect_start(request, platform: str):
    try:
        return redirect(services.build_connect_url(user=request.user, platform=platform))
    except HookdError as exc:
        messages.error(request, exc.message)
        return redirect("connections")


@login_required
def oauth_callback(request, platform: str):
    """Platforms redirect the browser here with ?code&state, or ?error when the creator cancels."""
    label = platform.capitalize()
    error = request.GET.get("error")
    code = request.GET.get("code")
    state = request.GET.get("state")

    if error:
        if error == "access_denied":
            message = f"You cancelled the {label} connection."
        else:
            message = request.GET.get("error_description") or f"{label} returned an error."
    elif not code or not state:
        message = "The connection link is incomplete. Please start again."
    else:
        try:
            services.complete_connection(user=request.user, platform=platform, code=code, state=state)
        except HookdError as exc:
            message = exc.message
        else:
            messages.success(request, f"Your {platform} account is connected.")
            return redirect("connections")

    return render(request, "integrations/oauth_callback.html", {"error_message": message}, status=400)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request, platform: str):
    """Platform webhook endpoint. GET is the subscription handshake, POST a signed event delivery."""
    try:
        if request.method == "GET":
            return HttpResponse(services.answer_webhook_challenge(platform=platform, params=request.GET))
        services.process_webhook(platform=platform, body=request.body, headers=request.headers)
    except WebhookVerificationError as exc:
        logger.warning("Rejected %s webhook: %s", platform, exc.message)
        return HttpResponseForbidden(exc.message)
    except HookdError as exc:
        logger.warning("Could not handle %s webhook: %s", platform, exc.message)
        return HttpResponse(status=404 if isinstance(exc, ProviderNotFound) else 400)
    return HttpResponse("OK")
