"""Thin HTTP client for the Instagram API with Unified Login (graph.facebook.com).

Responsible only for transport and error translation. It knows nothing about
Hookd models.
"""

import logging

import requests

from apps.integrations.exceptions import ProviderAPIError, RateLimitedError, TokenExpiredError

logger = logging.getLogger(__name__)

LOGIN_DIALOG_URL = "https://www.facebook.com/dialog/oauth"
GRAPH_BASE_URL = "https://graph.facebook.com"

# Graph API error code for an invalid or expired access token.
EXPIRED_TOKEN_ERROR_CODE = 190
# Graph API error codes for rate limits (4 app, 17 user, 32 page, 613 custom) and 368 (temporarily blocked
# for policy reasons, for example spam detection).
RATE_LIMIT_ERROR_CODES = frozenset({4, 17, 32, 613, 368})
HTTP_TOO_MANY_REQUESTS = 429


class InstagramClient:
    def __init__(self, *, api_version: str, timeout: int):
        self.api_version = api_version
        self.timeout = timeout
        self.session = requests.Session()

    def graph_url(self, path: str) -> str:
        return f"{GRAPH_BASE_URL}/{self.api_version}/{path.lstrip('/')}"

    def get(self, url: str, params: dict) -> dict:
        return self._request("GET", url, params=params)

    def post(self, url: str, data: dict) -> dict:
        return self._request("POST", url, data=data)

    def download(self, url: str, *, max_bytes: int) -> tuple[bytes, str]:
        """A CDN file as (bytes, content type), refused when it is larger than max_bytes."""
        try:
            with self.session.get(url, timeout=self.timeout, stream=True) as response:
                if not response.ok:
                    raise ProviderAPIError("Instagram did not return the file.")
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                data = b""
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    data += chunk
                    if len(data) > max_bytes:
                        raise ProviderAPIError("The file is too large.")
        except requests.RequestException as exc:
            logger.warning("Instagram download failed: %s", exc.__class__.__name__)
            raise ProviderAPIError("Could not reach Instagram. Please try again.") from exc
        return data, content_type

    def _request(self, method: str, url: str, *, params: dict | None = None, data: dict | None = None) -> dict:
        try:
            response = self.session.request(method, url, params=params, data=data, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning("Instagram request to %s failed: %s", url, exc.__class__.__name__)
            raise ProviderAPIError("Could not reach Instagram. Please try again.") from exc

        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.ok:
            return body

        raise self._translate_error(response.status_code, body)

    @staticmethod
    def _translate_error(http_status: int, body: dict) -> ProviderAPIError:
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        message = error.get("message") or "Instagram rejected the request."
        code = error.get("code")
        logger.warning("Instagram API error %s (code %s): %s", http_status, code, message)
        if code == EXPIRED_TOKEN_ERROR_CODE:
            return TokenExpiredError(message)
        if code in RATE_LIMIT_ERROR_CODES or http_status == HTTP_TOO_MANY_REQUESTS:
            return RateLimitedError(message)
        return ProviderAPIError(message)
