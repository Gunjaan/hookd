from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.model_querysets import create_user
from apps.ai import services
from apps.ai.claude import ClaudeError, ClaudeUnavailable, generate_json, image_block
from apps.ai.gemini import GeminiError, GeminiUnavailable, generate
from apps.ai.models import AiCall

PRICES = {"model": {"input": 1.0, "output": 10.0}}


def claude_response(text: str, stop_reason: str = "end_turn") -> mock.Mock:
    response = mock.Mock(stop_reason=stop_reason, content=[mock.Mock(type="text", text=text)])
    response.usage = mock.Mock(input_tokens=100, cache_read_input_tokens=20, cache_creation_input_tokens=None, output_tokens=30)
    return response


@override_settings(ANTHROPIC_API_KEY="key", AI_MODEL_PRICES=PRICES)
class ClaudeTests(TestCase):
    def ask(self, response=None, *, content="hi", error=None):
        with mock.patch("apps.ai.claude.anthropic.Anthropic") as client:
            client.return_value.messages.create.return_value = response
            client.return_value.messages.create.side_effect = error
            answer = generate_json("model", content, system="s", schema={}, max_output_tokens=10)
        return answer, client.return_value.messages.create.call_args.kwargs

    @override_settings(ANTHROPIC_API_KEY="")
    def test_missing_key_is_not_a_call(self):
        with self.assertRaises(ClaudeUnavailable):
            generate_json("model", "hi", system="s", schema={}, max_output_tokens=10)
        self.assertFalse(AiCall.objects.exists())

    def test_json_answer_is_recorded_with_tokens_and_cost(self):
        answer, sent = self.ask(claude_response('{"a": 1}'))
        self.assertEqual(answer, {"a": 1})
        self.assertEqual(sent["messages"], [{"role": "user", "content": "hi"}])
        call = AiCall.objects.get()
        self.assertEqual((call.provider, call.model, call.purpose, call.status), ("claude", "model", "other", "ok"))
        self.assertEqual((call.system, call.request), ("s", {"content": "hi", "max_output_tokens": 10}))
        self.assertEqual(call.response, {"stop_reason": "end_turn", "text": '{"a": 1}'})
        self.assertEqual((call.input_tokens, call.output_tokens), (120, 30))  # cache reads count as input
        self.assertEqual(call.cost_usd, Decimal("0.000420"))  # 120 * 1 + 30 * 10 per million

    def test_images_are_stored_as_type_and_size(self):
        self.ask(claude_response('{"a": 1}'), content=[image_block(b"jpeg-bytes", "image/jpeg"), {"type": "text", "text": "go"}])
        self.assertEqual(
            AiCall.objects.get().request["content"],
            [{"type": "image", "media_type": "image/jpeg", "bytes": 10}, {"type": "text", "text": "go"}],
        )

    def test_unusable_answers_are_errors_and_still_recorded(self):
        for response in (claude_response("not json"), claude_response('{"a": 1}', "refusal"), claude_response('{"a": 1}', "max_tokens")):
            with self.subTest(response.stop_reason), self.assertRaises(ClaudeError):
                self.ask(response)
        self.assertEqual(list(AiCall.objects.values_list("status", "input_tokens")), [("failed", 120)] * 3)

    def test_api_error_is_recorded(self):
        import anthropic

        with self.assertRaises(ClaudeError):
            self.ask(error=anthropic.APIConnectionError(request=mock.Mock()))
        call = AiCall.objects.get()
        self.assertEqual((call.status, call.response, call.cost_usd), ("failed", None, Decimal("0")))
        self.assertTrue(call.error)

    def test_attribution_and_its_end(self):
        user = create_user(email="creator@example.com", password="a-strong-pass-123")
        with services.attributed("comment_reply", user=user, subject=user):
            self.ask(claude_response('{"a": 1}'))
        self.ask(claude_response('{"a": 1}'))
        first, second = AiCall.objects.order_by("pk")
        self.assertEqual((first.purpose, first.user, first.subject), ("comment_reply", user, user))
        self.assertEqual((second.purpose, second.user, second.subject), ("other", None, None))

    @override_settings(AI_MODEL_PRICES={})
    def test_unknown_model_costs_zero(self):
        self.ask(claude_response('{"a": 1}'))
        self.assertEqual(AiCall.objects.get().cost_usd, Decimal("0"))

    def test_a_failed_record_never_breaks_the_call(self):
        with mock.patch("apps.ai.model_querysets.AiCall.objects.create", side_effect=RuntimeError("db down")):
            answer, _ = self.ask(claude_response('{"a": 1}'))
        self.assertEqual(answer, {"a": 1})


@override_settings(GEMINI_API_KEY="key", AI_MODEL_PRICES=PRICES)
class GeminiTests(TestCase):
    @override_settings(GEMINI_API_KEY="")
    def test_missing_key(self):
        with self.assertRaises(GeminiUnavailable):
            generate("model", ["hi"], None)

    def test_call_is_recorded_without_image_bytes(self):
        from google.genai import types

        image = types.Part.from_bytes(data=b"png-bytes", mime_type="image/png")
        response = mock.Mock(parts=[image], usage_metadata=mock.Mock(prompt_token_count=50, candidates_token_count=40, thoughts_token_count=10))
        config = types.GenerateContentConfig(response_modalities=["IMAGE"])
        with mock.patch("apps.ai.gemini.genai.Client") as client:
            client.return_value.models.generate_content.return_value = response
            generate("model", [types.Part.from_bytes(data=b"jpeg", mime_type="image/jpeg"), "Shot"], config)
        call = AiCall.objects.get()
        self.assertEqual(call.provider, "gemini")
        self.assertEqual(call.request["contents"], [{"type": "image", "media_type": "image/jpeg", "bytes": 4}, "Shot"])
        self.assertEqual(call.request["config"]["response_modalities"], ["IMAGE"])
        self.assertEqual(call.response, {"parts": [{"type": "image", "media_type": "image/png", "bytes": 9}]})
        self.assertEqual((call.input_tokens, call.output_tokens, call.cost_usd), (50, 50, Decimal("0.000550")))

    def test_api_error_is_recorded(self):
        from google.genai import errors, types

        with mock.patch("apps.ai.gemini.genai.Client") as client:
            client.return_value.models.generate_content.side_effect = errors.APIError(500, {})
            with self.assertRaises(GeminiError):
                generate("model", ["Shot"], types.GenerateContentConfig())
        self.assertEqual(AiCall.objects.get().status, "failed")


class AdminTests(TestCase):
    def test_list_shows_totals_and_filters_by_subject(self):
        user = create_user(email="creator@example.com", password="a-strong-pass-123")
        with services.attributed("comment_reply", subject=user):
            services.record(provider="claude", model="m", request={}, input_tokens=10, output_tokens=5)
        services.record(provider="claude", model="m", request={}, input_tokens=1, output_tokens=1)
        self.client.force_login(get_user_model().objects.create_superuser(email="admin@example.com", password="a-strong-pass-123"))
        listing = self.client.get(reverse("admin:ai_aicall_changelist"))
        self.assertContains(listing, "Filtered calls: 11 input tokens, 6 output tokens")
        call = AiCall.objects.get(input_tokens=10)
        filtered = self.client.get(
            reverse("admin:ai_aicall_changelist"),
            {"subject_type__id__exact": call.subject_type_id, "subject_id__exact": user.pk},
        )
        self.assertContains(filtered, "Filtered calls: 10 input tokens, 5 output tokens")
        self.assertEqual(self.client.get(reverse("admin:ai_aicall_change", args=[call.pk])).status_code, 200)
