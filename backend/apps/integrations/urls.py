from django.urls import path

from apps.integrations import api
from apps.integrations.views import connect_start, connections_page, oauth_callback, webhook

urlpatterns = [
    path("connections/", connections_page, name="connections"),
    path("connections/<slug:platform>/connect/", connect_start, name="connect-start"),
    path("api/connections/accounts/<int:account_id>/refresh/", api.account_refresh, name="api-account-refresh"),
    path("api/connections/accounts/<int:account_id>/subscribe/", api.account_subscribe, name="api-account-subscribe"),
    path("api/connections/accounts/<int:account_id>/disconnect/", api.account_disconnect, name="api-account-disconnect"),
    # No trailing slash: must match INSTAGRAM_REDIRECT_URI as registered with Meta.
    path("auth/<slug:platform>/callback", oauth_callback, name="oauth-callback"),
    path("webhooks/<slug:platform>/", webhook, name="webhook"),
]
