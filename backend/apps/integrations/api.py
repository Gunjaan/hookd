"""JSON endpoints for the connections page (data-api forms in static/js/app.js). Business logic stays in services.py."""

from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from apps.core.exceptions import HookdError
from apps.core.http import api_login_required, json_error, json_ok
from apps.integrations import services
from apps.integrations.exceptions import AccountNotFound


def _provider_card(request, platform: str, message: str):
    """The platform's card redrawn (`fragments`) so badges, accounts and buttons match, plus a message."""
    item = next(item for item in services.connection_overview(request.user) if item.provider.platform == platform)
    html = render_to_string("integrations/provider_card.html", {"item": item}, request=request)
    return json_ok(fragments={f"provider-{platform}": html}, message=message)


def _account_action(request, account_id: int, action, message: str):
    try:
        account = services.get_user_account(user=request.user, account_id=account_id)
        account = action(account)
    except AccountNotFound as exc:
        return json_error(exc.message, status=404)
    except HookdError as exc:
        return json_error(exc.message)
    return _provider_card(request, account.platform, message.format(username=account.username))


@api_login_required
@require_POST
def account_refresh(request, account_id: int):
    return _account_action(request, account_id, services.refresh_account_token, "Token for @{username} refreshed.")


@api_login_required
@require_POST
def account_subscribe(request, account_id: int):
    return _account_action(
        request, account_id, services.subscribe_account_webhooks, "@{username} is subscribed to realtime events."
    )


@api_login_required
@require_POST
def account_disconnect(request, account_id: int):
    try:
        account = services.disconnect_account(user=request.user, account_id=account_id)
    except AccountNotFound as exc:
        return json_error(exc.message, status=404)
    return _provider_card(request, account.platform, f"@{account.username} was disconnected.")
