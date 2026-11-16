from django.contrib import admin

from apps.comments.models import Comment


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ["author_username", "text", "account", "is_from_account", "commented_at"]
    list_filter = ["is_from_account", "account__platform"]
    search_fields = ["text", "author_username", "external_id", "media_external_id"]
    raw_id_fields = ["account"]
    readonly_fields = ["payload", "created_at", "updated_at"]
