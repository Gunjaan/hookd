from django.contrib import admin

from apps.agent.models import AgentProfile, CommentReply, PostBrain, ReplyRule


@admin.register(AgentProfile)
class AgentProfileAdmin(admin.ModelAdmin):
    list_display = ("account", "enabled", "fallback_action", "paused_until", "next_send_at")
    list_filter = ("enabled",)


@admin.register(PostBrain)
class PostBrainAdmin(admin.ModelAdmin):
    list_display = ("post", "agent_enabled", "updated_at")


@admin.register(ReplyRule)
class ReplyRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "account", "post", "action", "enabled", "position")
    list_filter = ("action", "enabled")
    search_fields = ("name", "keywords")


@admin.register(CommentReply)
class CommentReplyAdmin(admin.ModelAdmin):
    list_display = ("comment", "status", "rule", "scheduled_for", "sent_at")
    list_filter = ("status",)
    search_fields = ("final_text", "comment__text", "comment__author_username")
    raw_id_fields = ("comment",)
