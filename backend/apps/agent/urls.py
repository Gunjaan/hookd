from django.urls import path

from apps.agent import api, views

urlpatterns = [
    path("agent/", views.agent_home, name="agent"),
    path("agent/accounts/<int:account_id>/", views.account_settings, name="agent-account"),
    path("agent/inbox/", views.inbox, name="agent-inbox"),
    path("agent/tickets/", views.tickets, name="agent-tickets"),
    path("api/agent/accounts/<int:account_id>/settings/", api.account_settings, name="api-agent-settings"),
    path("api/agent/accounts/<int:account_id>/enabled/", api.account_enabled, name="api-agent-enabled"),
    path("api/agent/accounts/<int:account_id>/preview/", api.preview, name="api-agent-preview"),
    path("api/agent/posts/<int:post_id>/brain/", api.post_brain, name="api-agent-post-brain"),
    path("api/agent/facts/<int:fact_id>/forget/", api.fact_forget, name="api-agent-fact-forget"),
    path("api/agent/replies/<int:reply_id>/approve/", api.reply_approve, name="api-reply-approve"),
    path("api/agent/replies/<int:reply_id>/reject/", api.reply_reject, name="api-reply-reject"),
    path("api/agent/replies/<int:reply_id>/retry/", api.reply_retry, name="api-reply-retry"),
    path("api/agent/dms/<int:dm_id>/approve/", api.dm_approve, name="api-dm-approve"),
    path("api/agent/dms/<int:dm_id>/reject/", api.dm_reject, name="api-dm-reject"),
    path("api/agent/questions/<int:question_id>/answer/", api.question_answer, name="api-question-answer"),
    path("api/agent/questions/<int:question_id>/dismiss/", api.question_dismiss, name="api-question-dismiss"),
    path("api/agent/tickets/<int:ticket_id>/", api.ticket_update, name="api-ticket-update"),
]
