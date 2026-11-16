"""Signed OAuth `state` values.

State binds the callback to the user and platform that started the flow, so
no server side storage is needed. It expires after STATE_MAX_AGE_SECONDS.
"""

import secrets

from django.core import signing

from apps.integrations.exceptions import OAuthStateError

STATE_SALT = "hookd.integrations.oauth-state"
STATE_MAX_AGE_SECONDS = 600


def issue_state(*, user_id: int, platform: str) -> str:
    payload = {"u": user_id, "p": platform, "n": secrets.token_urlsafe(8)}
    return signing.dumps(payload, salt=STATE_SALT, compress=True)


def validate_state(state: str, *, user_id: int, platform: str) -> None:
    try:
        payload = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE_SECONDS)
    except signing.SignatureExpired as exc:
        raise OAuthStateError("The connection request expired. Please try again.") from exc
    except signing.BadSignature as exc:
        raise OAuthStateError() from exc
    if payload.get("u") != user_id or payload.get("p") != platform:
        raise OAuthStateError()
