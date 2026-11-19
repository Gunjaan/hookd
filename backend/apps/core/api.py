"""JSON endpoints for the public pages (data-api forms in static/js/app.js)."""

from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from apps.core import services
from apps.core.exceptions import HookdError
from apps.core.forms import ContactForm
from apps.core.http import json_error, json_ok


@require_POST
def contact(request):
    """The landing page contact form. On success the form is replaced with a thank you note."""
    form = ContactForm(request.POST)
    if not form.is_valid():
        field, errors = next(iter(form.errors.items()))
        return json_error(f"{form.fields[field].label}: {errors[0]}")
    try:
        services.send_contact(form.cleaned_data, services.client_ip(request))
    except HookdError as exc:
        return json_error(exc.message, status=429)
    return json_ok(html=render_to_string("core/contact_sent.html", {"name": form.cleaned_data["name"]}))
