import hashlib
import hmac
import json

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.model_querysets import create_user
from apps.comments import model_querysets as queries
from apps.integrations import model_querysets as account_queries
from apps.integrations.providers.types import AccountProfile, TokenBundle

INSTAGRAM_TEST_SETTINGS = {
    "APP_ID": "123",
    "APP_SECRET": "secret",
    "LOGIN_CONFIG_ID": "456",
    "REDIRECT_URI": "https://hookd.test/auth/instagram/callback",
    "GRAPH_API_VERSION": "v27.0",
    "WEBHOOK_VERIFY_TOKEN": "verify-me",
}

ACCOUNT_ID = "17841400000000"


def comment_payload(comment_id="c1", author_id="999", text="Love this", account_id=ACCOUNT_ID):
    return {
        "object": "instagram",
        "entry": [
            {
                "id": account_id,
                "time": 1763208000,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "comment_id": comment_id,
                            "text": text,
                            "from": {"id": author_id, "username": "fan.one"},
                            "media": {"id": "m1", "media_product_type": "REELS"},
                            "parent_id": "",
                        },
                    }
                ],
            }
        ],
    }


@override_settings(INSTAGRAM=INSTAGRAM_TEST_SETTINGS)
class InstagramWebhookTests(TestCase):
    def setUp(self):
        user = create_user(email="creator@example.com", password="a-strong-pass-123")
        self.account = account_queries.upsert_account(
            user=user,
            platform="instagram",
            profile=AccountProfile(external_id=ACCOUNT_ID, username="asha.edits"),
            tokens=TokenBundle(access_token="suat"),
        )
        self.url = reverse("webhook", args=["instagram"])

    def _post(self, payload, secret="secret"):
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return self.client.post(self.url, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)

    def test_handshake_echoes_challenge(self):
        params = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1158201444"}
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"1158201444")

    def test_handshake_rejects_wrong_token(self):
        params = {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}
        self.assertEqual(self.client.get(self.url, params).status_code, 403)

    @override_settings(INSTAGRAM={**INSTAGRAM_TEST_SETTINGS, "WEBHOOK_VERIFY_TOKEN": ""})
    def test_handshake_rejected_when_token_not_configured(self):
        params = {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}
        self.assertEqual(self.client.get(self.url, params).status_code, 403)

    def test_signed_comment_is_stored_once(self):
        self.assertEqual(self._post(comment_payload()).status_code, 200)
        self.assertEqual(self._post(comment_payload()).status_code, 200)
        comments = list(queries.list_comments_for_account(self.account))
        self.assertEqual(len(comments), 1)
        comment = comments[0]
        self.assertEqual(comment.external_id, "c1")
        self.assertEqual(comment.text, "Love this")
        self.assertEqual(comment.author_username, "fan.one")
        self.assertEqual(comment.media_external_id, "m1")
        self.assertFalse(comment.is_from_account)
        self.assertEqual(comment.commented_at.year, 2025)

    def test_comment_by_the_account_itself_is_flagged(self):
        self._post(comment_payload(comment_id="c2", author_id=ACCOUNT_ID))
        self.assertTrue(queries.list_comments_for_account(self.account).get().is_from_account)

    def test_bad_signature_is_rejected(self):
        response = self._post(comment_payload(), secret="wrong")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(queries.list_comments_for_account(self.account).exists())

    def test_unknown_account_is_acknowledged_and_skipped(self):
        response = self._post(comment_payload(account_id="1"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(queries.list_comments_for_account(self.account).exists())

    def test_unknown_platform_returns_404(self):
        response = self.client.get(reverse("webhook", args=["myspace"]), {"hub.mode": "subscribe"})
        self.assertEqual(response.status_code, 404)
