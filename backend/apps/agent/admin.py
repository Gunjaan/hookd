from django.contrib import admin

from apps.agent.models import (
    AgentAction,
    AgentEvent,
    AgentProfile,
    AgentRun,
    CommentReply,
    LearnedFact,
    OutgoingDM,
    PostBrain,
    SellerQuestion,
    SupportTicket,
)


@admin.register(AgentProfile)
class AgentProfileAdmin(admin.ModelAdmin):
    list_display = ("account", "enabled", "approve_before_sending", "paused_until", "next_send_at")
    list_filter = ("enabled", "approve_before_sending")


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
    raw_id_fields = ("post", "source_post", "source_reply", "source_run")


class AgentEventInline(admin.TabularInline):
    model = AgentEvent
    extra = 0
    fields = ("kind", "payload", "available_at", "processed_at")
    readonly_fields = fields


class AgentActionInline(admin.TabularInline):
    model = AgentAction
    extra = 0
    fields = ("created_at", "tool", "input", "result", "is_error")
    readonly_fields = fields


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("__str__", "account", "status", "steps", "wake_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("buyer_username", "outcome", "comment__text")
    raw_id_fields = ("comment", "conversation")
    inlines = (AgentEventInline, AgentActionInline)


@admin.register(SellerQuestion)
class SellerQuestionAdmin(admin.ModelAdmin):
    list_display = ("question", "status", "created_at", "answered_at")
    list_filter = ("status",)
    raw_id_fields = ("run",)


@admin.register(OutgoingDM)
class OutgoingDMAdmin(admin.ModelAdmin):
    list_display = ("text", "kind", "status", "scheduled_for", "sent_at")
    list_filter = ("status", "kind")
    raw_id_fields = ("run",)


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("__str__", "account", "buyer_username", "product", "status", "emailed_at", "created_at")
    list_filter = ("status",)
    search_fields = ("summary", "product", "buyer_username")
    raw_id_fields = ("run",)
