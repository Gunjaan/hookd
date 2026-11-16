from django.contrib import admin

from apps.integrations.models import SocialAccount


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = ["username", "platform", "user", "status", "token_expires_at", "created_at"]
    list_filter = ["platform", "status"]
    search_fields = ["username", "external_id", "user__email"]
    raw_id_fields = ["user"]
    exclude = ["access_token", "refresh_token"]
    readonly_fields = ["external_id", "scopes", "metadata", "token_expires_at", "last_refreshed_at", "created_at", "updated_at"]
