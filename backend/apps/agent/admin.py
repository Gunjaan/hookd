from django.contrib import admin

from apps.agent.models import AgentProfile, CommentReply, LearnedFact, PostBrain


@admin.register(AgentProfile)
class AgentProfileAdmin(admin.ModelAdmin):
    list_display = ("account", "enabled", "paused_until", "next_send_at")
    list_filter = ("enabled",)


@admin.register(PostBrain)
class PostBrainAdmin(admin.ModelAdmin):
    list_display = ("post", "agent_enabled", "updated_at")


@admin.register(CommentReply)
class CommentReplyAdmin(admin.ModelAdmin):
    list_display = ("comment", "status", "scheduled_for", "sent_at")
    list_filter = ("status",)
    search_fields = ("final_text", "comment__text", "comment__author_username")
    raw_id_fields = ("comment",)


@admin.register(LearnedFact)
class LearnedFactAdmin(admin.ModelAdmin):
    list_display = ("label", "text", "account", "post", "source", "created_at")
    list_filter = ("source",)
    search_fields = ("label", "text")
    raw_id_fields = ("post", "source_post")
