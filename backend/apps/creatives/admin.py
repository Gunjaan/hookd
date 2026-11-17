from django.contrib import admin

from apps.creatives import model_querysets as queries
from apps.creatives.models import Creative, CreativeAiCall, CreativeSlide, CreativeSlideVersion, CreativeSource


class CreativeSourceInline(admin.TabularInline):
    model = CreativeSource
    extra = 0
    readonly_fields = ["token", "position", "file"]


class CreativeSlideInline(admin.TabularInline):
    model = CreativeSlide
    extra = 0
    readonly_fields = ["position", "role", "prompt", "tweak", "status", "image", "error"]


class CreativeAiCallInline(admin.TabularInline):
    model = CreativeAiCall
    extra = 0
    can_delete = False
    fields = ["created_at", "kind", "slide", "model", "input_tokens", "output_tokens", "cost_usd"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Creative)
class CreativeAdmin(admin.ModelAdmin):
    list_display = ["pk", "user", "status", "look", "format_choice", "tokens", "cost", "created_at"]
    list_filter = ["status", "look", "format_choice"]
    search_fields = ["caption", "notes", "error"]
    raw_id_fields = ["user", "publish_job"]
    readonly_fields = ["brief", "created_at", "updated_at"]
    inlines = [CreativeSourceInline, CreativeSlideInline, CreativeAiCallInline]

    def get_queryset(self, request):
        return queries.with_usage_totals(super().get_queryset(request))

    @admin.display(description="AI tokens", ordering="total_tokens")
    def tokens(self, obj):
        return obj.total_tokens

    @admin.display(description="AI cost (USD)", ordering="total_cost")
    def cost(self, obj):
        return f"{obj.total_cost:.4f}"


class CreativeSlideVersionInline(admin.TabularInline):
    model = CreativeSlideVersion
    extra = 0
    readonly_fields = ["created_at", "image", "tweak"]


@admin.register(CreativeSlide)
class CreativeSlideAdmin(admin.ModelAdmin):
    list_display = ["pk", "creative", "position", "role", "status", "updated_at"]
    list_filter = ["status"]
    raw_id_fields = ["creative"]
    readonly_fields = ["image", "created_at", "updated_at"]
    inlines = [CreativeSlideVersionInline]


@admin.register(CreativeAiCall)
class CreativeAiCallAdmin(admin.ModelAdmin):
    """Every billed post studio AI call. Filter by user or date: the totals line covers the filtered calls."""

    list_display = ["created_at", "user", "creative", "kind", "model", "input_tokens", "output_tokens", "cost_usd"]
    list_filter = ["kind", "model", ("creative__user", admin.RelatedOnlyFieldListFilter), "created_at"]
    list_select_related = ["creative__user"]
    date_hierarchy = "created_at"
    raw_id_fields = ["creative", "slide"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="User", ordering="creative__user")
    def user(self, obj):
        return obj.creative.user

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        changelist = getattr(response, "context_data", {}).get("cl")
        if changelist is not None:
            totals = queries.usage_totals(changelist.queryset)
            totals["cost"] = f"{totals['cost']:.4f}"
            response.context_data["usage_totals"] = totals
        return response
