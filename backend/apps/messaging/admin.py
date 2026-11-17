from django.contrib import admin

from apps.messaging.models import Conversation, DirectMessage


class DirectMessageInline(admin.TabularInline):
    model = DirectMessage
    extra = 0
    fields = ["sent_at", "is_from_account", "kind", "text", "story_external_id"]
    readonly_fields = fields
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["participant_external_id", "account", "last_customer_message_at", "reply_window_ends_at"]
    list_filter = ["account__platform"]
    search_fields = ["participant_external_id"]
    raw_id_fields = ["account"]
    inlines = [DirectMessageInline]


@admin.register(DirectMessage)
class DirectMessageAdmin(admin.ModelAdmin):
    list_display = ["text", "kind", "is_from_account", "conversation", "sent_at"]
    list_filter = ["kind", "is_from_account"]
    search_fields = ["text", "external_id", "story_external_id"]
    raw_id_fields = ["conversation"]
    readonly_fields = ["payload", "created_at", "updated_at"]
