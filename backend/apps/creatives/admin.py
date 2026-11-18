from django.contrib import admin
from apps.ai import model_querysets as ai_queries
from apps.ai.admin import calls_link
from apps.creatives.models import Creative, CreativeSlide, CreativeSlideVersion, CreativeSource


class CreativeSourceInline(admin.TabularInline):
    model = CreativeSource
    extra = 0
    readonly_fields = ["token", "position", "file"]


class CreativeSlideInline(admin.TabularInline):
    model = CreativeSlide
    extra = 0
    readonly_fields = ["position", "role", "prompt", "tweak", "status", "image", "error"]


@admin.register(Creative)
class CreativeAdmin(admin.ModelAdmin):
    list_display = ["pk", "user", "status", "look", "format_choice", "tokens", "cost", "created_at"]
    list_filter = ["status", "look", "format_choice"]
    search_fields = ["caption", "notes", "error"]
    raw_id_fields = ["user", "publish_job"]
    readonly_fields = ["brief", "ai_calls", "created_at", "updated_at"]
    inlines = [CreativeSourceInline, CreativeSlideInline]

    def get_queryset(self, request):
        return ai_queries.with_usage_totals(super().get_queryset(request))

    @admin.display(description="AI tokens", ordering="total_tokens")
    def tokens(self, obj):
        return obj.total_tokens

    @admin.display(description="AI cost (USD)", ordering="total_cost")
    def cost(self, obj):
        return f"{obj.total_cost:.4f}"

    @admin.display(description="AI calls")
    def ai_calls(self, obj):
        return calls_link(obj)
