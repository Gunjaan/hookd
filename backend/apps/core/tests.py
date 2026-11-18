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
    def test_landing_has_link_preview_tags(self):
        response = self.client.get(reverse("home"), HTTP_HOST="localhost")
        self.assertContains(response, '<meta property="og:title" content="Hookd: your very own agent to get the world hookd" />')
        self.assertContains(response, '<meta property="og:url" content="http://localhost/" />')
        self.assertContains(response, '<meta property="og:image" content="http://localhost/static/img/og-image.png" />')
        self.assertContains(response, '<meta name="twitter:card" content="summary_large_image" />')
        self.assertContains(response, '<link rel="canonical" href="http://localhost/" />')
        self.assertContains(response, '<meta name="robots" content="index, follow" />')

    def test_signed_in_pages_are_not_indexed(self):
        user = create_user(email="meta@example.com", password="pass-12345")
        self.client.force_login(user)
        response = self.client.get(reverse("connections"))
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow" />')

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


def claude_response(text: str, stop_reason: str = "end_turn") -> mock.Mock:
    response = mock.Mock(stop_reason=stop_reason, content=[mock.Mock(type="text", text=text)])
    response.usage = mock.Mock(input_tokens=100, cache_read_input_tokens=20, cache_creation_input_tokens=None, output_tokens=30)
    return response


@override_settings(ANTHROPIC_API_KEY="key")
class ClaudeTests(TestCase):
    def ask(self, response):
        from apps.core.claude import generate_json

        with mock.patch("apps.core.claude.anthropic.Anthropic") as client:
            client.return_value.messages.create.return_value = response
            answer = generate_json("model", "hi", system="s", schema={}, max_output_tokens=10)
        return answer, client.return_value.messages.create.call_args.kwargs

    @override_settings(ANTHROPIC_API_KEY="")
    def test_missing_key(self):
        from apps.core.claude import ClaudeUnavailable, generate_json

        with self.assertRaises(ClaudeUnavailable):
            generate_json("model", "hi", system="s", schema={}, max_output_tokens=10)

    def test_non_json_answer_is_an_error(self):
        from apps.core.claude import ClaudeError

        with self.assertRaises(ClaudeError):
            self.ask(claude_response("not json"))

    def test_refusal_and_cut_off_answers_are_errors(self):
        from apps.core.claude import ClaudeError

        for stop_reason in ("refusal", "max_tokens"):
            with self.subTest(stop_reason), self.assertRaises(ClaudeError):
                self.ask(claude_response('{"a": 1}', stop_reason))

    def test_json_answer_carries_token_usage(self):
        answer, sent = self.ask(claude_response('{"a": 1}'))
        self.assertEqual(answer.data, {"a": 1})
        self.assertEqual((answer.usage.model, answer.usage.input_tokens, answer.usage.output_tokens), ("model", 120, 30))
        self.assertEqual(sent["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(sent["output_config"]["format"], {"type": "json_schema", "schema": {}})
