from django.urls import path

from apps.agent import views

urlpatterns = [
    path("agent/", views.agent_home, name="agent"),
    path("agent/accounts/<int:account_id>/", views.account_settings, name="agent-account"),
    path("agent/accounts/<int:account_id>/preview/", views.preview, name="agent-preview"),
    path("agent/posts/<int:post_id>/brain/", views.post_brain, name="agent-post-brain"),
    path("agent/inbox/", views.inbox, name="agent-inbox"),
    path("agent/replies/<int:reply_id>/approve/", views.reply_approve, name="agent-reply-approve"),
    path("agent/replies/<int:reply_id>/reject/", views.reply_reject, name="agent-reply-reject"),
    path("agent/replies/<int:reply_id>/retry/", views.reply_retry, name="agent-reply-retry"),
]
