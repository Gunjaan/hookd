import io
from unittest import mock
from urllib.parse import parse_qs, urlparse

import requests
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.model_querysets import create_user
from apps.integrations import model_querysets as queries
from apps.integrations.exceptions import OAuthStateError
from apps.integrations.oauth_state import issue_state, validate_state
from apps.integrations.providers.types import AccountProfile, TokenBundle

INSTAGRAM_TEST_SETTINGS = {
    "APP_ID": "123",
    "APP_SECRET": "secret",
    "LOGIN_CONFIG_ID": "456",
    "REDIRECT_URI": "https://hookd.test/auth/instagram/callback",
    "GRAPH_API_VERSION": "v27.0",
}


class OAuthStateTests(TestCase):
    def test_round_trip(self):
        state = issue_state(user_id=7, platform="instagram")
        validate_state(state, user_id=7, platform="instagram")

    def test_rejects_other_user_or_platform(self):
        state = issue_state(user_id=7, platform="instagram")
        with self.assertRaises(OAuthStateError):
            validate_state(state, user_id=8, platform="instagram")
        with self.assertRaises(OAuthStateError):
            validate_state(state, user_id=7, platform="youtube")

    def test_rejects_tampered_state(self):
        with self.assertRaises(OAuthStateError):
            validate_state("not-a-real-state", user_id=7, platform="instagram")


class EncryptedTokenTests(TestCase):
    def test_tokens_are_encrypted_at_rest(self):
        user = create_user(email="c@example.com", password="a-strong-pass-123")
        account = queries.upsert_account(
            user=user,
            platform="instagram",
            profile=AccountProfile(external_id="1784"),
            tokens=TokenBundle(access_token="plain-token"),
        )
        stored = queries.stored_access_token_ciphertext(account.pk)
        self.assertNotIn("plain-token", stored)
        self.assertEqual(queries.find_account("instagram", "1784").access_token, "plain-token")


class FakeGraphMixin:
    """Fakes the Graph API calls of the Instagram connect flow."""

    subscribe_response = {"success": True}

    def _fake_graph(self, method, url, params=None, data=None):
        self.assertTrue(url.startswith("https://graph.facebook.com/v27.0/"), url)
        path = url.removeprefix("https://graph.facebook.com/v27.0/")
        if method == "POST":
            self.assertEqual(path, "17841400000000/subscribed_apps")
            self.assertEqual(data, {"subscribed_fields": "comments", "access_token": "suat"})
            self.subscribed = True
            return self._response(self.subscribe_response)
        self.assertEqual(method, "GET")
        if path == "oauth/access_token":
            self.assertEqual(params["code"], "abc")
            self.assertEqual(params["client_id"], "123")
            return self._response({"access_token": "suat", "token_type": "bearer"})
        self.assertEqual(params["access_token"], "suat")
        if path == "me":
            return self._response({"id": "555", "client_business_id": "777"})
        if path == "me/assigned_instagram_accounts":
            return self._response({"data": [{"id": "17841400000000", "username": "asha.edits"}]})
        if path == "17841400000000":
            return self._response(
                {"id": "17841400000000", "username": "asha.edits", "name": "Asha", "followers_count": 12}
            )
        raise AssertionError(f"Unexpected request {method} {url}")

    @staticmethod
    def _response(body, status=200):
        response = mock.Mock(ok=status < 400, status_code=status)
        response.json.return_value = body
        return response

    def _patch_graph(self):
        return mock.patch.object(
            requests.Session,
            "request",
            side_effect=lambda method, url, params=None, data=None, timeout=None: self._fake_graph(
                method, url, params, data
            ),
        )


@override_settings(INSTAGRAM=INSTAGRAM_TEST_SETTINGS)
class ConnectionPageTests(FakeGraphMixin, TestCase):
    def setUp(self):
        self.user = create_user(email="creator@example.com", password="a-strong-pass-123")
        self.client.force_login(self.user)

    def _callback(self, code="abc"):
        state = issue_state(user_id=self.user.pk, platform="instagram")
        with self._patch_graph():
            return self.client.get(reverse("oauth-callback", args=["instagram"]), {"code": code, "state": state})

    def test_connections_page_lists_providers(self):
        response = self.client.get(reverse("connections"))
        self.assertContains(response, "Connect Instagram")
        self.assertContains(response, "Comment replies")

    def test_pages_require_login(self):
        self.client.logout()
        response = self.client.get(reverse("connections"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('connections')}")

    def test_connect_redirects_to_meta_with_signed_state(self):
        response = self.client.post(reverse("connect-start", args=["instagram"]))
        self.assertEqual(response.status_code, 302)
        url = urlparse(response["Location"])
        query = parse_qs(url.query)
        self.assertEqual(url.netloc, "www.facebook.com")
        self.assertEqual(url.path, "/dialog/oauth")
        self.assertEqual(query["client_id"], ["123"])
        self.assertEqual(query["config_id"], ["456"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["prompt"], ["login"])
        self.assertNotIn("scope", query)
        self.assertEqual(query["redirect_uri"], [INSTAGRAM_TEST_SETTINGS["REDIRECT_URI"]])
        validate_state(query["state"][0], user_id=self.user.pk, platform="instagram")

    def test_connect_unknown_platform_shows_error(self):
        response = self.client.post(reverse("connect-start", args=["myspace"]), follow=True)
        self.assertContains(response, "This platform is not supported.")

    @override_settings(INSTAGRAM={**INSTAGRAM_TEST_SETTINGS, "APP_SECRET": ""})
    def test_unconfigured_provider_cannot_connect(self):
        self.assertContains(self.client.get(reverse("connections")), "Not configured")
        response = self.client.post(reverse("connect-start", args=["instagram"]), follow=True)
        self.assertContains(response, "Instagram credentials are missing on the server.")

    def test_callback_completes_connection(self):
        response = self._callback(code="abc#_=_")
        self.assertRedirects(response, reverse("connections"), fetch_redirect_response=False)
        account = queries.find_account("instagram", "17841400000000")
        self.assertEqual(account.access_token, "suat")
        self.assertIsNone(account.token_expires_at)
        self.assertEqual(account.metadata["client_business_id"], "777")
        self.assertEqual(account.metadata["followers_count"], 12)
        self.assertTrue(self.subscribed)
        self.assertEqual(account.status_detail, "")
        page = self.client.get(reverse("connections"))
        self.assertContains(page, "Your instagram account is connected.")
        self.assertContains(page, "@asha.edits")
        self.assertContains(page, "12 followers")

    def test_failed_webhook_subscription_keeps_account_with_detail(self):
        self.subscribe_response = {"success": False}
        response = self._callback()
        self.assertRedirects(response, reverse("connections"), fetch_redirect_response=False)
        account = queries.find_account("instagram", "17841400000000")
        self.assertEqual(account.status, "active")
        self.assertIn("webhook subscription", account.status_detail)

    def test_callback_without_shared_account_fails(self):
        original = self._fake_graph

        def no_accounts(method, url, params=None, data=None):
            if url.endswith("/me/assigned_instagram_accounts"):
                return self._response({"data": []})
            return original(method, url, params, data)

        self._fake_graph = no_accounts
        response = self._callback()
        self.assertContains(response, "Connection failed", status_code=400)
        self.assertIsNone(queries.find_account("instagram", "17841400000000"))

    def test_callback_rejects_account_owned_by_someone_else(self):
        other = create_user(email="other@example.com", password="a-strong-pass-123")
        queries.upsert_account(
            user=other,
            platform="instagram",
            profile=AccountProfile(external_id="17841400000000"),
            tokens=TokenBundle(access_token="x"),
        )
        response = self._callback()
        self.assertContains(response, "already connected to another Hookd user", status_code=400)
        self.assertEqual(queries.find_account("instagram", "17841400000000").user_id, other.pk)

    def test_callback_shows_cancel_and_bad_state(self):
        url = reverse("oauth-callback", args=["instagram"])
        self.assertContains(
            self.client.get(url, {"error": "access_denied"}), "You cancelled the Instagram connection.", status_code=400
        )
        self.assertContains(
            self.client.get(url, {"code": "abc", "state": "forged"}), "The connection request expired", status_code=400
        )

    def test_callback_requires_login_and_keeps_query(self):
        self.client.logout()
        url = reverse("oauth-callback", args=["instagram"])
        response = self.client.get(url, {"code": "abc", "state": "s"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(f"{reverse('login')}?next="))
        self.assertIn("code%3Dabc", response["Location"])

    def test_account_row_offers_reconnect_but_not_refresh(self):
        queries.upsert_account(
            user=self.user,
            platform="instagram",
            profile=AccountProfile(external_id="17841400000000", username="asha.edits"),
            tokens=TokenBundle(access_token="old"),
        )
        page = self.client.get(reverse("connections"))
        self.assertContains(page, "Reconnect")
        self.assertContains(page, "Resubscribe")
        self.assertContains(page, f'action="{reverse("connect-start", args=["instagram"])}"')
        self.assertNotContains(page, ">Refresh<")

    def test_reconnect_updates_existing_account_in_place(self):
        existing = queries.upsert_account(
            user=self.user,
            platform="instagram",
            profile=AccountProfile(external_id="17841400000000", username="asha.edits"),
            tokens=TokenBundle(access_token="old"),
        )
        self._callback()
        account = queries.find_account("instagram", "17841400000000")
        self.assertEqual(account.pk, existing.pk)
        self.assertEqual(account.access_token, "suat")
        self.assertTrue(self.subscribed)

    def _stored_account(self, detail=""):
        account = queries.upsert_account(
            user=self.user,
            platform="instagram",
            profile=AccountProfile(external_id="17841400000000", username="asha.edits"),
            tokens=TokenBundle(access_token="suat"),
        )
        return queries.set_account_status(account, "active", detail)

    def test_resubscribe_uses_stored_token_and_clears_warning(self):
        account = self._stored_account(detail="Webhook setup incomplete: boom")
        with self._patch_graph():
            response = self.client.post(reverse("account-subscribe", args=[account.pk]), follow=True)
        self.assertContains(response, "@asha.edits is subscribed to realtime events.")
        self.assertTrue(self.subscribed)
        self.assertEqual(queries.find_account("instagram", "17841400000000").status_detail, "")

    def test_resubscribe_failure_is_shown_and_saved(self):
        account = self._stored_account()
        self.subscribe_response = {"success": False}
        with self._patch_graph():
            response = self.client.post(reverse("account-subscribe", args=[account.pk]), follow=True)
        self.assertContains(response, "did not confirm the webhook subscription")
        self.assertIn("Webhook setup incomplete", queries.find_account("instagram", "17841400000000").status_detail)

    def test_resubscribe_only_own_accounts(self):
        other = create_user(email="other@example.com", password="a-strong-pass-123")
        foreign = queries.upsert_account(
            user=other,
            platform="instagram",
            profile=AccountProfile(external_id="1"),
            tokens=TokenBundle(access_token="x"),
        )
        response = self.client.post(reverse("account-subscribe", args=[foreign.pk]), follow=True)
        self.assertContains(response, "Connected account not found.")

    def test_subscribe_webhooks_command(self):
        self._stored_account()
        out = io.StringIO()
        with self._patch_graph():
            call_command("subscribe_webhooks", stdout=out)
        self.assertIn("Subscribed 1 account(s).", out.getvalue())

    def test_disconnect_only_affects_own_accounts(self):
        own = queries.upsert_account(
            user=self.user,
            platform="instagram",
            profile=AccountProfile(external_id="2", username="mine"),
            tokens=TokenBundle(access_token="x"),
        )
        other = create_user(email="other@example.com", password="a-strong-pass-123")
        foreign = queries.upsert_account(
            user=other,
            platform="instagram",
            profile=AccountProfile(external_id="1"),
            tokens=TokenBundle(access_token="x"),
        )
        response = self.client.post(reverse("account-disconnect", args=[foreign.pk]), follow=True)
        self.assertContains(response, "Connected account not found.")
        self.assertIsNotNone(queries.get_account_for_user(other, foreign.pk))

        response = self.client.post(reverse("account-disconnect", args=[own.pk]), follow=True)
        self.assertContains(response, "@mine was disconnected.")
        self.assertIsNone(queries.get_account_for_user(self.user, own.pk))
