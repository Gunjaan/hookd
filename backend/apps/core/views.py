from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.core.forms import ContactForm
from apps.core.model_querysets import database_is_available


@require_GET
def health(request):
    """Liveness check for the host or load balancer."""
    db_ok = database_is_available()
    return JsonResponse({"status": "ok" if db_ok else "degraded", "database": db_ok}, status=200 if db_ok else 503)


@require_GET
def landing(request):
    """Public homepage, the first page everyone lands on."""
    return render(
        request, "core/landing.html", {"signups_open": settings.SIGNUPS_OPEN, "contact_form": ContactForm()}
    )
