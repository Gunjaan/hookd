from django.urls import path

from apps.agent import api, views

urlpatterns = [
    path("agent/", views.agent_home, name="agent"),
    path("agent/accounts/<int:account_id>/", views.account_settings, name="agent-account"),
    path("agent/inbox/", views.inbox, name="agent-inbox"),
    path("api/agent/accounts/<int:account_id>/settings/", api.account_settings, name="api-agent-settings"),
    path("api/agent/accounts/<int:account_id>/enabled/", api.account_enabled, name="api-agent-enabled"),
    path("api/agent/accounts/<int:account_id>/preview/", api.preview, name="api-agent-preview"),
    path("api/agent/posts/<int:post_id>/brain/", api.post_brain, name="api-agent-post-brain"),
    path("api/agent/replies/<int:reply_id>/approve/", api.reply_approve, name="api-reply-approve"),
    path("api/agent/replies/<int:reply_id>/reject/", api.reply_reject, name="api-reply-reject"),
    path("api/agent/replies/<int:reply_id>/retry/", api.reply_retry, name="api-reply-retry"),
]
