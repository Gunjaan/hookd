from django.urls import path

from apps.integrations.views import (
    account_disconnect,
    account_refresh,
    account_subscribe,
    connect_start,
    connections_page,
    oauth_callback,
    webhook,
)

urlpatterns = [
    path("connections/", connections_page, name="connections"),
    path("connections/<slug:platform>/connect/", connect_start, name="connect-start"),
    path("connections/accounts/<int:account_id>/refresh/", account_refresh, name="account-refresh"),
    path("connections/accounts/<int:account_id>/subscribe/", account_subscribe, name="account-subscribe"),
    path("connections/accounts/<int:account_id>/disconnect/", account_disconnect, name="account-disconnect"),
    # No trailing slash: must match INSTAGRAM_REDIRECT_URI as registered with Meta.
    path("auth/<slug:platform>/callback", oauth_callback, name="oauth-callback"),
    path("webhooks/<slug:platform>/", webhook, name="webhook"),
]
