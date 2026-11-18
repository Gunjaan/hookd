from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.html import format_html

from apps.ai import model_querysets as queries
from apps.ai.models import AiCall

SUBJECT_LOOKUPS = ("subject_type__id__exact", "subject_id__exact")


def calls_link(obj) -> str:
    """A link to the AI calls made about `obj`, for other admin pages."""
    url = reverse("admin:ai_aicall_changelist")
    subject_type = ContentType.objects.get_for_model(obj)
    count = queries.calls_about(obj).count()
    return format_html(
        '<a href="{}?subject_type__id__exact={}&subject_id__exact={}">{} calls</a>', url, subject_type.pk, obj.pk, count
    )


@admin.register(AiCall)
class AiCallAdmin(admin.ModelAdmin):
    """Every AI provider call. Filter by purpose, user or date: the totals line covers the filtered calls."""

    list_display = [
        "created_at", "purpose", "provider", "model", "status", "user", "account", "subject",
        "input_tokens", "output_tokens", "cost_usd", "duration_ms",
    ]
    list_filter = [
        "purpose", "provider", "model", "status", ("user", admin.RelatedOnlyFieldListFilter), "subject_type", "created_at",
    ]
    list_select_related = ["user", "account", "subject_type"]
    date_hierarchy = "created_at"
    search_fields = ["error", "system"]
    fields = [
        "created_at", "provider", "model", "purpose", "status", "error", "user", "account", "subject_type",
        "subject_id", "input_tokens", "output_tokens", "cost_usd", "duration_ms", "system", "request", "response",
    ]
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def lookup_allowed(self, lookup, value, request=None):
        return lookup in SUBJECT_LOOKUPS or super().lookup_allowed(lookup, value, request)

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        changelist = getattr(response, "context_data", {}).get("cl")
        if changelist is not None:
            totals = queries.usage_totals(changelist.queryset)
            totals["cost"] = f"{totals['cost']:.4f}"
            response.context_data["usage_totals"] = totals
        return response
