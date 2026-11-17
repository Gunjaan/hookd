from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.accounts.model_querysets import create_user
from apps.core import crypto


class HealthViewTests(TestCase):
    def test_reports_database_status(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": True})


class LandingViewTests(TestCase):
    def test_guest_sees_landing(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your very own agent to get the world")
        self.assertContains(response, 'id="pipeline"')
        self.assertContains(response, 'id="learns"')
        self.assertContains(response, "Hooky")
        self.assertContains(response, 'id="faq"')
        self.assertContains(response, "data-highlights")
        self.assertContains(response, "data-highlight")
        self.assertContains(response, "js/landing.js")

    def test_signed_in_user_sees_landing_with_studio_link(self):
        user = create_user(email="creator@example.com", password="a-strong-pass-123")
        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("studio"))


class CryptoTests(SimpleTestCase):
    def tearDown(self):
        crypto._cipher.cache_clear()

    def test_round_trip_and_key_rotation(self):
        old_key, new_key = crypto.generate_key(), crypto.generate_key()
        with override_settings(FIELD_ENCRYPTION_KEYS=[old_key]):
            crypto._cipher.cache_clear()
            ciphertext = crypto.encrypt("secret")
        with override_settings(FIELD_ENCRYPTION_KEYS=[new_key, old_key]):
            crypto._cipher.cache_clear()
            self.assertEqual(crypto.decrypt(ciphertext), "secret")

    def test_unknown_key_raises(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[crypto.generate_key()]):
            crypto._cipher.cache_clear()
            ciphertext = crypto.encrypt("secret")
        with override_settings(FIELD_ENCRYPTION_KEYS=[crypto.generate_key()]):
            crypto._cipher.cache_clear()
            with self.assertRaises(crypto.DecryptionError):
                crypto.decrypt(ciphertext)


class GeminiTests(TestCase):
    @override_settings(GEMINI_API_KEY="")
    def test_missing_key(self):
        from apps.core.gemini import GeminiUnavailable, generate

        with self.assertRaises(GeminiUnavailable):
            generate("model", ["hi"], None)

    @override_settings(GEMINI_API_KEY="key")
    def test_non_json_answer_is_an_error(self):
        from apps.core.gemini import GeminiError, generate_json

        with mock.patch("apps.core.gemini.genai.Client") as client:
            client.return_value.models.generate_content.return_value.text = "not json"
            with self.assertRaises(GeminiError):
                generate_json("model", ["hi"], system="s", schema={}, max_output_tokens=10)

    @override_settings(GEMINI_API_KEY="key")
    def test_json_answer_carries_token_usage(self):
        from apps.core.gemini import generate_json

        with mock.patch("apps.core.gemini.genai.Client") as client:
            response = client.return_value.models.generate_content.return_value
            response.text = '{"a": 1}'
            response.usage_metadata.prompt_token_count = 120
            response.usage_metadata.candidates_token_count = 30
            response.usage_metadata.thoughts_token_count = None
            answer = generate_json("model", ["hi"], system="s", schema={}, max_output_tokens=10)
        self.assertEqual(answer.data, {"a": 1})
        self.assertEqual((answer.usage.model, answer.usage.input_tokens, answer.usage.output_tokens), ("model", 120, 30))
