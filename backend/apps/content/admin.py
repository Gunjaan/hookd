from django.contrib import admin

from apps.content.models import Post, PublishAsset, PublishJob


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ["external_id", "account", "product_type", "media_type", "like_count", "comments_count", "posted_at"]
    list_filter = ["product_type", "media_type", "account__platform"]
    search_fields = ["external_id", "caption"]
    raw_id_fields = ["account"]
    readonly_fields = ["insights", "insights_fetched_at", "synced_at", "created_at", "updated_at"]


class PublishAssetInline(admin.TabularInline):
    model = PublishAsset
    extra = 0
    readonly_fields = ["token", "position", "file", "content_type"]


@admin.register(PublishJob)
class PublishJobAdmin(admin.ModelAdmin):
    list_display = ["pk", "account", "kind", "status", "created_at", "published_at"]
    list_filter = ["status", "kind"]
    search_fields = ["caption", "publish_id", "error"]
    raw_id_fields = ["account", "post"]
    inlines = [PublishAssetInline]
